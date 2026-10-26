import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';
import 'shared_dialogs.dart';

Future<void> openUserConversation(
  BuildContext context,
  AdminApi api,
  int userId,
) => showDialog<void>(
  context: context,
  builder: (_) => _ConversationDialog(api: api, userId: userId),
);

class MessagingPage extends StatefulWidget {
  const MessagingPage({
    super.key,
    required this.api,
    this.initialUserId,
    this.onFocusHandled,
  });

  final AdminApi api;
  final int? initialUserId;
  final VoidCallback? onFocusHandled;

  @override
  State<MessagingPage> createState() => _MessagingPageState();
}

class _MessagingPageState extends State<MessagingPage> {
  final TextEditingController _bc = TextEditingController();
  final TextEditingController _recipient = TextEditingController();
  final TextEditingController _privateText = TextEditingController();
  bool _busy = false;
  Map<String, dynamic>? _broadcastJob;

  // القوالب: النص هو المنبع + محرر لكل صف
  List<String> _tpl = [];
  final List<TextEditingController> _tplC = [];
  bool _tplLoading = true;
  bool _tplDirty = false;
  List<Map<String, dynamic>> _conversations = [];
  String _conversationQuery = '';
  Timer? _conversationTimer;

  @override
  void dispose() {
    _bc.dispose();
    _recipient.dispose();
    _privateText.dispose();
    _conversationTimer?.cancel();
    for (final controller in _tplC) {
      controller.dispose();
    }
    super.dispose();
  }

  @override
  void initState() {
    super.initState();
    _loadTemplates();
    _loadConversations();
    _conversationTimer = Timer.periodic(
      const Duration(seconds: 15),
      (_) => _loadConversations(),
    );
    final userId = widget.initialUserId;
    if (userId != null) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (!mounted) return;
        widget.onFocusHandled?.call();
        unawaited(_showConversation(userId));
      });
    }
  }

  Future<void> _loadConversations() async {
    try {
      final result = await widget.api.get('/api/admin/conversations', {
        if (_conversationQuery.trim().isNotEmpty)
          'q': _conversationQuery.trim(),
        'limit': '100',
      });
      if (!mounted) return;
      setState(() {
        _conversations = (result['items'] as List? ?? const [])
            .map((item) => Map<String, dynamic>.from(item as Map))
            .toList();
      });
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    }
  }

  void _rebuildControllers(List<String> items) {
    for (final c in _tplC) {
      c.dispose();
    }
    _tplC.clear();
    for (final t in items) {
      _tplC.add(TextEditingController(text: t));
    }
  }

  Future<void> _loadTemplates() async {
    try {
      final d = await widget.api.get('/api/admin/templates');
      if (!mounted) return;
      final items = List<String>.from(d['items'] ?? <dynamic>[]);
      _rebuildControllers(items);
      setState(() {
        _tpl = items;
        _tplLoading = false;
        _tplDirty = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _tplLoading = false);
      toast(context, errMsg(e), error: true);
    }
  }

  Future<void> _addTemplate() async {
    final text = await requiredTextDialog(
      context,
      title: 'قالب رد جديد',
      hint: 'نص القالب (2–300 حرف)…',
      maxLines: 2,
    );
    if (text == null || text.length < 2) return;
    setState(() {
      _tpl.add(text);
      _tplC.add(TextEditingController(text: text));
      _tplDirty = true;
    });
  }

  void _delTemplate(int i) {
    setState(() {
      _tpl.removeAt(i);
      _tplC[i].dispose();
      _tplC.removeAt(i);
      _tplDirty = true;
    });
  }

  Future<void> _saveTemplates() async {
    final items = <String>[
      for (final c in _tplC) c.text.trim(),
    ].where((t) => t.length >= 2).toList();
    if (items.length > 30) {
      toast(context, 'عدد القوالب يتجاوز السقف (30)', error: true);
      return;
    }
    final ok = await confirmDialog(
      context,
      'حفظ القوالب',
      'حفظ ${items.length} قالباً؟ تظهر فورًا في نافذة الرد.',
      okLabel: 'حفظ',
    );
    if (!ok) return;
    setState(() => _busy = true);
    try {
      await widget.api.put('/api/admin/templates', {'items': items});
      if (!mounted) return;
      toast(context, 'حُفظت القوالب ✅ تظهر فورًا في نافذة رد أي بلاغ');
      await _loadTemplates();
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _sendBroadcast() async {
    final t = _bc.text.trim();
    if (t.length < 3) {
      toast(context, 'النص قصير جداً — 3 أحرف فأكثر', error: true);
      return;
    }
    final ok = await confirmDialog(
      context,
      'تأكيد التعميم',
      'سيصل التعميم إلى جميع مستخدمي البوت المسجلين (${t.length} حرفاً). هل تتابع الإرسال؟',
      okLabel: 'أرسل',
    );
    if (!ok) return;
    setState(() => _busy = true);
    try {
      final d = await widget.api.post('/api/admin/broadcast', {'text': t});
      if (!mounted) return;
      if (d['job_id'] is! int) {
        toast(context, 'لم يُنشأ تعميم بالخلفية', error: true);
        return;
      }
      setState(() => _broadcastJob = Map<String, dynamic>.from(d));
      _bc.clear();
      await _watchBroadcast(d['job_id'] as int);
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _watchBroadcast(int jobId) async {
    while (mounted) {
      await Future<void>.delayed(const Duration(seconds: 2));
      if (!mounted) return;
      try {
        final job = await widget.api.get('/api/admin/broadcast/$jobId');
        if (!mounted) return;
        setState(() => _broadcastJob = job);
        if (job['status'] != 'running' && job['status'] != 'queued') {
          final completed = job['status'] == 'completed';
          toast(
            context,
            '${completed ? 'اكتمل' : 'توقف'} التعميم: نجح ${job['sent']}، '
            'تعذّر ${job['failed']} من ${job['total']}',
            error: !completed,
          );
          return;
        }
      } catch (e) {
        if (mounted) toast(context, errMsg(e), error: true);
        return;
      }
    }
  }

  Future<void> _openPrivateConversation() async {
    final uid = int.tryParse(_recipient.text.trim());
    final text = _privateText.text.trim();
    if (uid == null || uid <= 0) {
      toast(context, 'أدخل معرف مستخدم تيليجرام صحيحاً', error: true);
      return;
    }
    if (text.isEmpty || text.length > 1000) {
      toast(context, 'اكتب رسالة من 1 إلى 1000 حرف', error: true);
      return;
    }
    final confirmed = await confirmDialog(
      context,
      'إرسال رسالة خاصة',
      'سيتم إرسال الرسالة إلى المستخدم $uid وفتح سجل محادثة معه.',
      okLabel: 'إرسال وفتح المحادثة',
    );
    if (!confirmed || !mounted) return;
    setState(() => _busy = true);
    try {
      final result = await widget.api.post('/api/admin/users/$uid/message', {
        'text': text,
      });
      if (!mounted) return;
      if (result['sent'] != true) {
        toast(context, 'لم يؤكد الخادم تسليم الرسالة', error: true);
        return;
      }
      _privateText.clear();
      await _showConversation(uid);
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _showConversation(int uid) async {
    await showDialog<void>(
      context: context,
      builder: (_) => _ConversationDialog(api: widget.api, userId: uid),
    );
    if (mounted) await _loadConversations();
  }

  Widget _conversationTile(Map<String, dynamic> conversation) {
    final userId = (conversation['user_id'] as num).toInt();
    final name = conversation['full_name']?.toString().trim() ?? '';
    final unread = (conversation['unread_count'] as num? ?? 0).toInt();
    final lastMessage = conversation['last_message']?.toString() ?? '';
    final incoming = conversation['last_direction'] == 'in';
    return ListTile(
      contentPadding: EdgeInsets.zero,
      leading: const CircleAvatar(child: Icon(Icons.person_outline)),
      title: Text(
        name.isEmpty ? 'مستخدم $userId' : name,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      subtitle: Text(
        '${incoming ? 'المستخدم: ' : 'الإدارة: '}$lastMessage',
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (conversation['contact_enabled'] == false)
            const Padding(
              padding: EdgeInsetsDirectional.only(end: 8),
              child: Icon(
                Icons.do_not_disturb_on_outlined,
                size: 18,
                color: Colors.orange,
              ),
            ),
          if (unread > 0) Badge(label: Text('$unread')),
          const Icon(Icons.chevron_right),
        ],
      ),
      onTap: () => _showConversation(userId),
    );
  }

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        SectionCard(
          'محادثات المستخدمين',
          icon: Icons.forum_outlined,
          trailing: IconButton(
            tooltip: 'تحديث المحادثات',
            onPressed: _loadConversations,
            icon: const Icon(Icons.refresh),
          ),
          child: Column(
            children: [
              TextField(
                decoration: const InputDecoration(
                  labelText: 'بحث بالاسم أو المعرّف',
                  prefixIcon: Icon(Icons.search),
                ),
                onChanged: (value) {
                  _conversationQuery = value;
                  _loadConversations();
                },
              ),
              const SizedBox(height: 8),
              if (_conversations.isEmpty)
                const Padding(
                  padding: EdgeInsets.all(12),
                  child: Text('لا توجد محادثات محفوظة بعد.'),
                )
              else
                for (final conversation in _conversations)
                  _conversationTile(conversation),
            ],
          ),
        ),
        const SizedBox(height: 10),
        SectionCard(
          'محادثة خاصة مع مستخدم',
          icon: Icons.forum_outlined,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(
                controller: _recipient,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(
                  labelText: 'معرف مستخدم تيليجرام',
                  hintText: 'أدخل المعرف الرقمي للمستخدم',
                ),
              ),
              const SizedBox(height: 8),
              TextField(
                controller: _privateText,
                maxLines: 3,
                maxLength: 1000,
                decoration: const InputDecoration(
                  labelText: 'الرسالة الخاصة',
                  alignLabelWithHint: true,
                ),
              ),
              Align(
                alignment: AlignmentDirectional.centerEnd,
                child: FilledButton.icon(
                  onPressed: _busy ? null : _openPrivateConversation,
                  icon: const Icon(Icons.send_outlined),
                  label: const Text('إرسال وفتح سجل المحادثة'),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 10),
        SectionCard(
          'تعميم إلى جميع مستخدمي البوت',
          icon: Icons.campaign_outlined,
          trailing: _busy
              ? const SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : null,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(
                controller: _bc,
                maxLines: 4,
                maxLength: 1000,
                enabled: !_busy,
                decoration: const InputDecoration(
                  labelText: 'نص التعميم (3–1000 حرف)',
                  hintText:
                      'مثال: عاجل — يعمل النظام بالمعدل الطبيعي ابتداءً من مساء اليوم…',
                  alignLabelWithHint: true,
                ),
              ),
              const SizedBox(height: 4),
              const Text(
                'يُرسل إلى كل من بدأ استخدام البوت أو سبق تسجيله، مع حفظ تقدم الإرسال.',
              ),
              if (_broadcastJob != null) ...[
                const SizedBox(height: 8),
                LinearProgressIndicator(
                  value: (_broadcastJob!['total'] as num? ?? 0) == 0
                      ? null
                      : (((_broadcastJob!['sent'] as num? ?? 0) +
                                    (_broadcastJob!['failed'] as num? ?? 0)) /
                                (_broadcastJob!['total'] as num))
                            .clamp(0.0, 1.0)
                            .toDouble(),
                ),
                const SizedBox(height: 4),
                Text(
                  'الحالة: ${_broadcastJob!['status']} — '
                  '${_broadcastJob!['sent'] ?? 0} ناجح، '
                  '${_broadcastJob!['failed'] ?? 0} متعذّر من '
                  '${_broadcastJob!['total'] ?? 0}',
                ),
              ],
              const SizedBox(height: 8),
              FilledButton.icon(
                onPressed: _busy ? null : _sendBroadcast,
                icon: const Icon(Icons.send),
                label: const Text('إرسال التعميم'),
              ),
            ],
          ),
        ),
        const SizedBox(height: 10),
        SectionCard(
          'قوالب الردود الجاهزة',
          icon: Icons.format_quote_outlined,
          trailing: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              IconButton(
                tooltip: 'إضافة قالب',
                onPressed: _addTemplate,
                icon: const Icon(Icons.add_circle_outline),
              ),
              const SizedBox(width: 4),
              if (_tplDirty)
                TextButton.icon(
                  onPressed: _busy ? null : _saveTemplates,
                  icon: const Icon(Icons.save_outlined, size: 18),
                  label: const Text('حفظ'),
                ),
            ],
          ),
          child: _tplLoading
              ? const Center(child: CircularProgressIndicator())
              : _tpl.isEmpty
              ? const Text('لا قوالب بعد — أضف أول قالب بزر (+)')
              : Column(
                  children: [
                    for (int i = 0; i < _tpl.length; i++)
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Expanded(
                            child: TextField(
                              controller: _tplC[i],
                              maxLines: 2,
                              maxLength: 300,
                              onChanged: (_) =>
                                  setState(() => _tplDirty = true),
                            ),
                          ),
                          IconButton(
                            tooltip: 'استخدام نص التعميم',
                            onPressed: _busy
                                ? null
                                : () {
                                    final text = _tplC[i].text;
                                    _bc.value = TextEditingValue(
                                      text: text,
                                      selection: TextSelection.collapsed(
                                        offset: text.length,
                                      ),
                                    );
                                    toast(
                                      context,
                                      'نُسخ القالب إلى نص التعميم',
                                    );
                                  },
                            icon: const Icon(Icons.campaign_outlined),
                          ),
                          IconButton(
                            tooltip: 'حذف القالب',
                            onPressed: () => _delTemplate(i),
                            icon: const Icon(
                              Icons.remove_circle_outline,
                              color: Color(0xFFE06C75),
                            ),
                          ),
                        ],
                      ),
                    const SizedBox(height: 4),
                    const Text(
                      'القوالب تُستخدم للرد السريع في تفصيل أي بلاغ — زر "قوالب" يعبّئ الرد فوراً',
                      style: TextStyle(fontSize: 12),
                    ),
                  ],
                ),
        ),
      ],
    );
  }
}

class _ConversationDialog extends StatefulWidget {
  const _ConversationDialog({required this.api, required this.userId});

  final AdminApi api;
  final int userId;

  @override
  State<_ConversationDialog> createState() => _ConversationDialogState();
}

class _ConversationDialogState extends State<_ConversationDialog> {
  final TextEditingController _message = TextEditingController();
  List<Map<String, dynamic>> _items = [];
  Timer? _timer;
  String? _error;
  bool _loading = true;
  bool _sending = false;
  bool _fetching = false;

  @override
  void initState() {
    super.initState();
    _load();
    _timer = Timer.periodic(const Duration(seconds: 5), (_) => _load());
  }

  @override
  void dispose() {
    _timer?.cancel();
    _message.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    if (_fetching) return;
    _fetching = true;
    try {
      final result = await widget.api.get(
        '/api/admin/users/${widget.userId}/messages',
      );
      if (!mounted) return;
      final items = (result['items'] as List? ?? const [])
          .map((item) => Map<String, dynamic>.from(item as Map))
          .toList();
      setState(() {
        _items = items;
        _error = null;
        _loading = false;
      });
      final lastVisibleInboundId = items
          .where((item) => item['direction'] == 'in')
          .map((item) => (item['id'] as num?)?.toInt() ?? 0)
          .fold<int>(0, (latest, id) => id > latest ? id : latest);
      await widget.api.post('/api/admin/users/${widget.userId}/messages/read', {
        'last_message_id': lastVisibleInboundId,
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = errMsg(e);
        _loading = false;
      });
    } finally {
      _fetching = false;
    }
  }

  Future<void> _send() async {
    final text = _message.text.trim();
    if (text.isEmpty || text.length > 1000) return;
    setState(() => _sending = true);
    try {
      await widget.api.post('/api/admin/users/${widget.userId}/message', {
        'text': text,
      });
      if (!mounted) return;
      _message.clear();
      await _load();
    } catch (e) {
      if (mounted) {
        if (e is ApiException && e.status == 502) _message.clear();
        toast(context, _deliveryError(errMsg(e)), error: true);
        await _load();
      }
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  Future<void> _retry(int messageId) async {
    try {
      await widget.api.post(
        '/api/admin/users/${widget.userId}/messages/$messageId/retry',
        {},
      );
      if (mounted) toast(context, 'أُعيد إرسال الرسالة بنجاح');
      await _load();
    } catch (error) {
      if (mounted) {
        toast(context, _deliveryError(errMsg(error)), error: true);
        await _load();
      }
    }
  }

  String _deliveryError(String code) => switch (code) {
    'user_blocked_bot' => 'تعذّر الإرسال: المستخدم حظر البوت.',
    'recipient_unavailable' => 'تعذّر الإرسال: محادثة المستخدم غير متاحة.',
    'telegram_network_error' =>
      'تعذّر الاتصال بتيليجرام؛ يمكنك إعادة المحاولة.',
    'direct_contact_disabled' => 'التواصل المباشر متوقف لهذا المستخدم.',
    'bot_not_ready' =>
      'البوت غير جاهز الآن؛ بقيت الرسالة محفوظة لإعادة المحاولة.',
    'telegram_send_failed' =>
      'تعذّر الإرسال؛ بقيت الرسالة محفوظة لإعادة المحاولة.',
    _ => code,
  };

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text('محادثة المستخدم ${widget.userId}'),
    content: SizedBox(
      width: 600,
      height: 480,
      child: Column(
        children: [
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _error != null && _items.isEmpty
                ? Center(child: Text(_error!))
                : _items.isEmpty
                ? const Center(child: Text('لا توجد رسائل بعد.'))
                : ListView.builder(
                    itemCount: _items.length,
                    itemBuilder: (context, index) {
                      final item = _items[index];
                      final outgoing = item['direction'] == 'out';
                      return Align(
                        alignment: outgoing
                            ? AlignmentDirectional.centerStart
                            : AlignmentDirectional.centerEnd,
                        child: Card(
                          color: outgoing
                              ? Theme.of(context).colorScheme.primaryContainer
                              : Theme.of(
                                  context,
                                ).colorScheme.surfaceContainerHighest,
                          child: Padding(
                            padding: const EdgeInsets.all(10),
                            child: ConstrainedBox(
                              constraints: const BoxConstraints(maxWidth: 450),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(item['text']?.toString() ?? ''),
                                  const SizedBox(height: 4),
                                  Text(
                                    '${outgoing ? 'الإدارة' : 'المستخدم'} · '
                                    '${item['created_at'] ?? ''}',
                                    style: Theme.of(
                                      context,
                                    ).textTheme.labelSmall,
                                  ),
                                  if (outgoing &&
                                      item['delivery_status'] == 'failed') ...[
                                    const SizedBox(height: 4),
                                    Text(
                                      _deliveryError(
                                        item['error_code']?.toString() ??
                                            'telegram_send_failed',
                                      ),
                                      style: TextStyle(
                                        color: Theme.of(
                                          context,
                                        ).colorScheme.error,
                                      ),
                                    ),
                                    Align(
                                      alignment: AlignmentDirectional.centerEnd,
                                      child: TextButton.icon(
                                        onPressed: () =>
                                            _retry((item['id'] as num).toInt()),
                                        icon: const Icon(
                                          Icons.refresh,
                                          size: 16,
                                        ),
                                        label: const Text('إعادة الإرسال'),
                                      ),
                                    ),
                                  ] else if (outgoing) ...[
                                    const SizedBox(height: 3),
                                    Text(
                                      item['delivery_status'] == 'delivered'
                                          ? 'أُرسل عبر تيليجرام'
                                          : 'حالة الإرسال غير معروفة',
                                      style: Theme.of(
                                        context,
                                      ).textTheme.labelSmall,
                                    ),
                                  ],
                                ],
                              ),
                            ),
                          ),
                        ),
                      );
                    },
                  ),
          ),
          if (_error != null && _items.isNotEmpty)
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: Text(
                _error!,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _message,
                  maxLength: 1000,
                  minLines: 1,
                  maxLines: 3,
                  decoration: const InputDecoration(
                    labelText: 'اكتب رسالة',
                    counterText: '',
                  ),
                ),
              ),
              const SizedBox(width: 8),
              IconButton.filled(
                onPressed: _sending ? null : _send,
                tooltip: 'إرسال',
                icon: _sending
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.send),
              ),
            ],
          ),
        ],
      ),
    ),
    actions: [
      TextButton(onPressed: _load, child: const Text('تحديث')),
      TextButton(
        onPressed: () => Navigator.of(context).pop(),
        child: const Text('إغلاق'),
      ),
    ],
  );
}

// ==================================================================
// 6) التصنيفات: محرر قائمة أنواع المخالفات (يقرأها البوت فوراً)
// ==================================================================
