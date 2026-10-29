import 'dart:async';
import 'dart:convert';
import 'dart:html' as html;
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';
import 'message_composer.dart';
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
  List<Map<String, dynamic>> _broadcastHistory = [];
  bool _canManageMessages = false;
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
    _loadBroadcastHistory();
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

  Future<void> _loadBroadcastHistory() async {
    try {
      final session = await widget.api.get('/api/admin/session');
      if (!mounted) return;
      final canManage = session['role'] == 'owner';
      if (!canManage) {
        setState(() {
          _canManageMessages = false;
          _broadcastHistory = [];
        });
        return;
      }
      final result = await widget.api.get('/api/admin/broadcast', {
        'limit': '30',
      });
      if (!mounted) return;
      setState(() {
        _canManageMessages = true;
        _broadcastHistory = (result['items'] as List? ?? const [])
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

  Future<void> _watchBroadcast(int jobId, {String kind = 'broadcast'}) async {
    while (mounted) {
      await Future<void>.delayed(const Duration(seconds: 2));
      if (!mounted) return;
      try {
        final job = await widget.api.get('/api/admin/broadcast/$jobId');
        if (!mounted) return;
        setState(() => _broadcastJob = job);
        final progressStatus = kind == 'delete'
            ? job['deletion_status']
            : kind == 'edit'
            ? job['edit_status']
            : job['status'];
        if (progressStatus != 'running' && progressStatus != 'queued') {
          final completed =
              progressStatus == 'completed' || progressStatus == null;
          toast(
            context,
            kind == 'delete'
                ? 'اكتمل حذف ${job['deletion_deleted'] ?? 0} رسالة، '
                      'وتعذّر ${job['deletion_failed'] ?? 0}'
                : kind == 'edit'
                ? 'اكتمل تعديل ${job['edit_edited'] ?? 0} رسالة، '
                      'وتعذّر ${job['edit_failed'] ?? 0}'
                : '${completed ? 'اكتمل' : 'توقف'} التعميم: '
                      'نجح ${job['sent']}، تعذّر ${job['failed']} من ${job['total']}',
            error: !completed,
          );
          await _loadBroadcastHistory();
          return;
        }
      } catch (e) {
        if (mounted) toast(context, errMsg(e), error: true);
        return;
      }
    }
  }

  Future<void> _deleteBroadcast(Map<String, dynamic> job) async {
    final id = (job['id'] as num).toInt();
    final confirmed = await confirmDialog(
      context,
      'حذف التعميم',
      'سيحاول البوت حذف الرسالة لدى كل مستلم خلال حد تيليجرام (48 ساعة). '
          'لا يمكن التراجع عن الحذف.',
      okLabel: 'حذف الرسائل',
      danger: true,
    );
    if (!confirmed || !mounted) return;
    try {
      final result = await widget.api.post(
        '/api/admin/broadcast/$id/delete',
        {},
      );
      if (result['status'] == 'running') {
        await _watchBroadcast(id, kind: 'delete');
      } else {
        toast(
          context,
          'لا توجد رسائل قابلة للحذف؛ منتهية الصلاحية: '
          '${result['expired'] ?? 0}',
        );
        await _loadBroadcastHistory();
      }
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    }
  }

  Future<void> _editBroadcast(Map<String, dynamic> job) async {
    final id = (job['id'] as num).toInt();
    final controller = TextEditingController();
    final value = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('تعديل الرسالة المرسلة'),
        content: TextField(
          controller: controller,
          autofocus: true,
          maxLines: 4,
          maxLength: job['media_type'] == 'text' || job['media_type'] == ''
              ? 1000
              : 900,
          decoration: const InputDecoration(
            labelText: 'النص الجديد',
            alignLabelWithHint: true,
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('إلغاء'),
          ),
          FilledButton(
            onPressed: () {
              if (controller.text.trim().isNotEmpty) {
                Navigator.pop(ctx, controller.text.trim());
              }
            },
            child: const Text('تعديل'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (value == null || !mounted) return;
    final confirmed = await confirmDialog(
      context,
      'تأكيد التعديل',
      'سيُحدّث النص لدى المستلمين الذين ما زالت رسائلهم قابلة للتعديل.',
      okLabel: 'تعديل الرسائل',
    );
    if (!confirmed || !mounted) return;
    try {
      final result = await widget.api.post('/api/admin/broadcast/$id/edit', {
        'text': value,
      });
      if (result['status'] == 'running') {
        await _watchBroadcast(id, kind: 'edit');
      } else {
        toast(
          context,
          'لا توجد رسائل قابلة للتعديل؛ منتهية الصلاحية: '
          '${result['expired'] ?? 0}',
        );
        await _loadBroadcastHistory();
      }
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
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
              : IconButton(
                  tooltip: 'تحديث سجل النشر',
                  onPressed: _loadBroadcastHistory,
                  icon: const Icon(Icons.refresh),
                ),
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
              if (_broadcastHistory.isNotEmpty) ...[
                const Divider(height: 24),
                Align(
                  alignment: AlignmentDirectional.centerStart,
                  child: Text(
                    'سجل الرسائل المرسلة',
                    style: Theme.of(context).textTheme.titleSmall,
                  ),
                ),
                for (final item in _broadcastHistory)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text(
                      'رسالة #${item['id']} · ${item['media_type'] ?? 'text'}',
                    ),
                    subtitle: Text(
                      '${item['created_at'] ?? ''} · '
                      'وصل ${item['sent'] ?? 0} من ${item['total'] ?? 0} · '
                      'قابلة للتعديل ${item['editable_messages'] ?? 0} · '
                      'قابلة للحذف ${item['deletable_messages'] ?? 0}',
                    ),
                    trailing: _canManageMessages
                        ? Wrap(
                            spacing: 2,
                            children: [
                              IconButton(
                                tooltip: 'تعديل الرسائل لدى المستلمين',
                                onPressed:
                                    ((item['editable_messages'] as num?)
                                                ?.toInt() ??
                                            0) >
                                        0
                                    ? () => _editBroadcast(item)
                                    : null,
                                icon: const Icon(Icons.edit_outlined),
                              ),
                              IconButton(
                                tooltip: 'حذف الرسائل لدى المستلمين',
                                onPressed:
                                    ((item['deletable_messages'] as num?)
                                                ?.toInt() ??
                                            0) >
                                        0
                                    ? () => _deleteBroadcast(item)
                                    : null,
                                icon: const Icon(
                                  Icons.delete_outline,
                                  color: Color(0xFFE06C75),
                                ),
                              ),
                            ],
                          )
                        : null,
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
  Uint8List? _mediaBytes;
  String? _filename;
  String _mediaType = 'photo';
  Timer? _timer;
  String? _error;
  bool _loading = true;
  bool _sending = false;
  bool _fetching = false;
  bool _canManageMessages = false;
  final Map<int, Future<Uint8List>> _photoRequests = {};

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
        _canManageMessages = result['can_manage_messages'] == true;
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
    if ((text.isEmpty && _mediaBytes == null) || text.length > 1000) return;
    setState(() => _sending = true);
    try {
      if (_mediaBytes != null) {
        await widget.api.upload(
          '/api/admin/users/${widget.userId}/media',
          fields: {'media_type': _mediaType, 'caption': text},
          filename: _filename,
          bytes: _mediaBytes,
        );
        _mediaBytes = null;
        _filename = null;
      } else {
        await widget.api.post('/api/admin/users/${widget.userId}/message', {
          'text': text,
        });
      }
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

  Future<void> _pickMedia() async {
    final accept = switch (_mediaType) {
      'photo' => 'image/jpeg,image/png',
      'video' => 'video/mp4',
      'audio' => 'audio/*',
      'voice' => '.ogg,.oga,audio/ogg',
      _ => '*/*',
    };
    final picker = html.FileUploadInputElement()
      ..accept = accept
      ..multiple = false;
    picker.click();
    await picker.onChange.first;
    final files = picker.files;
    if (files == null || files.isEmpty) return;
    final file = files.first;
    final maxBytes = _mediaType == 'photo'
        ? 10 * 1024 * 1024
        : 20 * 1024 * 1024;
    if (file.size <= 0 || file.size > maxBytes) {
      if (mounted) {
        toast(
          context,
          file.size <= 0
              ? 'الملف المحدد فارغ'
              : 'حجم الملف يتجاوز الحد المسموح',
          error: true,
        );
      }
      return;
    }
    final reader = html.FileReader()..readAsDataUrl(file);
    await reader.onLoad.first;
    final result = reader.result;
    if (result is! String || !result.contains(',')) {
      if (mounted) toast(context, 'تعذرت قراءة الملف', error: true);
      return;
    }
    try {
      final bytes = base64Decode(result.substring(result.indexOf(',') + 1));
      if (!mounted) return;
      setState(() {
        _mediaBytes = bytes;
        _filename = file.name;
      });
    } on FormatException {
      if (mounted) toast(context, 'تعذر تحليل الملف المحدد', error: true);
    }
  }

  Widget _buildComposer(double dialogWidth) {
    return MessageComposer(
      controller: _message,
      sending: _sending,
      mediaBytes: _mediaBytes,
      filename: _filename,
      mediaType: _mediaType,
      width: dialogWidth,
      onSend: _send,
      onPickMedia: (type) {
        setState(() {
          _mediaType = type;
          _mediaBytes = null;
          _filename = null;
        });
        unawaited(_pickMedia());
      },
      onRemoveMedia: () => setState(() {
        _mediaBytes = null;
        _filename = null;
      }),
    );
  }

  Future<void> _editMessage(Map<String, dynamic> item) async {
    final controller = TextEditingController(text: item['text']?.toString());
    final value = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('تعديل الرسالة'),
        content: TextField(
          controller: controller,
          autofocus: true,
          maxLines: 4,
          maxLength: 1000,
          decoration: const InputDecoration(labelText: 'النص الجديد'),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('إلغاء'),
          ),
          FilledButton(
            onPressed: () {
              if (controller.text.trim().isNotEmpty) {
                Navigator.pop(ctx, controller.text.trim());
              }
            },
            child: const Text('حفظ التعديل'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (value == null || !mounted) return;
    try {
      await widget.api.put(
        '/api/admin/users/${widget.userId}/messages/${item['id']}',
        {'text': value},
      );
      await _load();
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    }
  }

  Future<void> _deleteMessage(Map<String, dynamic> item) async {
    final confirmed = await confirmDialog(
      context,
      'حذف رسالة المستخدم',
      'سيحاول البوت حذف الرسالة لدى المستخدم. الحذف متاح خلال 48 ساعة فقط.',
      okLabel: 'حذف',
      danger: true,
    );
    if (!confirmed || !mounted) return;
    try {
      await widget.api.del(
        '/api/admin/users/${widget.userId}/messages/${item['id']}',
        {},
      );
      await _load();
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    }
  }

  Future<void> _downloadMedia(Map<String, dynamic> item) async {
    try {
      await widget.api.download(
        '/api/admin/users/${widget.userId}/messages/${item['id']}/media',
      );
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    }
  }

  Future<Uint8List> _messagePhoto(int messageId) => _photoRequests.putIfAbsent(
    messageId,
    () => widget.api.getBytes(
      '/api/admin/users/${widget.userId}/messages/$messageId/media',
    ),
  );

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
  Widget build(BuildContext context) {
    final screen = MediaQuery.sizeOf(context);
    final dialogWidth = screen.width > 1160 ? 1080.0 : screen.width * .94;
    final dialogHeight = screen.height > 860 ? 760.0 : screen.height * .88;
    return Dialog(
      insetPadding: EdgeInsets.all(screen.width < 500 ? 8 : 20),
      child: SizedBox(
        width: dialogWidth,
        height: dialogHeight,
        child: Column(
          children: [
            Padding(
              padding: const EdgeInsetsDirectional.fromSTEB(20, 10, 12, 8),
              child: Row(
                children: [
                  CircleAvatar(
                    backgroundColor: Theme.of(
                      context,
                    ).colorScheme.primaryContainer,
                    child: Icon(
                      Icons.person_outline,
                      color: Theme.of(context).colorScheme.onPrimaryContainer,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'محادثة المستخدم ${widget.userId}',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                        Text(
                          'رسائل مباشرة عبر البوت · تحديث تلقائي',
                          style: Theme.of(context).textTheme.labelSmall,
                        ),
                      ],
                    ),
                  ),
                  IconButton(
                    tooltip: 'تحديث المحادثة',
                    onPressed: _loading ? null : _load,
                    icon: const Icon(Icons.refresh),
                  ),
                  IconButton(
                    tooltip: 'إغلاق المحادثة',
                    onPressed: () => Navigator.of(context).pop(),
                    icon: const Icon(Icons.close),
                  ),
                ],
              ),
            ),
            const Divider(height: 1),
            Expanded(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
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
                                    elevation: 0,
                                    shape: RoundedRectangleBorder(
                                      borderRadius:
                                          BorderRadiusDirectional.only(
                                            topStart: const Radius.circular(18),
                                            topEnd: const Radius.circular(18),
                                            bottomStart: Radius.circular(
                                              outgoing ? 4 : 18,
                                            ),
                                            bottomEnd: Radius.circular(
                                              outgoing ? 18 : 4,
                                            ),
                                          ).resolve(Directionality.of(context)),
                                    ),
                                    color: outgoing
                                        ? Theme.of(
                                            context,
                                          ).colorScheme.primaryContainer
                                        : Theme.of(
                                            context,
                                          ).colorScheme.surfaceContainerHighest,
                                    child: Padding(
                                      padding: const EdgeInsets.all(10),
                                      child: ConstrainedBox(
                                        constraints: const BoxConstraints(
                                          maxWidth: 450,
                                        ),
                                        child: Column(
                                          crossAxisAlignment:
                                              CrossAxisAlignment.start,
                                          children: [
                                            if (item['is_deleted'] == true ||
                                                item['is_deleted'] == 1)
                                              const Text('تم حذف هذه الرسالة')
                                            else if ((item['text']
                                                        ?.toString() ??
                                                    '')
                                                .isNotEmpty)
                                              SelectableText(
                                                item['text'].toString(),
                                              ),
                                            if (item['has_media'] == true &&
                                                item['media_type'] == 'photo' &&
                                                item['is_deleted'] != true &&
                                                item['is_deleted'] != 1) ...[
                                              const SizedBox(height: 8),
                                              ClipRRect(
                                                borderRadius:
                                                    BorderRadius.circular(12),
                                                child: FutureBuilder<Uint8List>(
                                                  future: _messagePhoto(
                                                    (item['id'] as num).toInt(),
                                                  ),
                                                  builder: (context, snapshot) {
                                                    if (snapshot.hasData) {
                                                      return Image.memory(
                                                        snapshot.data!,
                                                        width: dialogWidth > 440
                                                            ? 360
                                                            : dialogWidth - 80,
                                                        height:
                                                            dialogWidth > 440
                                                            ? 240
                                                            : dialogWidth * .55,
                                                        fit: BoxFit.cover,
                                                      );
                                                    }
                                                    if (snapshot.hasError) {
                                                      return TextButton.icon(
                                                        onPressed: () =>
                                                            _downloadMedia(
                                                              item,
                                                            ),
                                                        icon: const Icon(
                                                          Icons
                                                              .download_outlined,
                                                        ),
                                                        label: const Text(
                                                          'تعذر عرض الصورة · تنزيل',
                                                        ),
                                                      );
                                                    }
                                                    return const SizedBox(
                                                      width: 240,
                                                      height: 120,
                                                      child: Center(
                                                        child:
                                                            CircularProgressIndicator(),
                                                      ),
                                                    );
                                                  },
                                                ),
                                              ),
                                            ],
                                            if (item['has_media'] == true) ...[
                                              const SizedBox(height: 4),
                                              TextButton.icon(
                                                onPressed: () =>
                                                    _downloadMedia(item),
                                                icon: const Icon(
                                                  Icons.download_outlined,
                                                  size: 16,
                                                ),
                                                label: Text(
                                                  'تنزيل ${item['filename'] ?? item['media_type'] ?? 'الوسيط'}',
                                                ),
                                              ),
                                            ],
                                            const SizedBox(height: 4),
                                            Text(
                                              '${outgoing ? 'الإدارة' : 'المستخدم'} · '
                                              '${item['created_at'] ?? ''}',
                                              style: Theme.of(
                                                context,
                                              ).textTheme.labelSmall,
                                            ),
                                            if (outgoing &&
                                                item['delivery_status'] ==
                                                    'failed') ...[
                                              const SizedBox(height: 4),
                                              Text(
                                                _deliveryError(
                                                  item['error_code']
                                                          ?.toString() ??
                                                      'telegram_send_failed',
                                                ),
                                                style: TextStyle(
                                                  color: Theme.of(
                                                    context,
                                                  ).colorScheme.error,
                                                ),
                                              ),
                                              Align(
                                                alignment: AlignmentDirectional
                                                    .centerEnd,
                                                child: TextButton.icon(
                                                  onPressed: () => _retry(
                                                    (item['id'] as num).toInt(),
                                                  ),
                                                  icon: const Icon(
                                                    Icons.refresh,
                                                    size: 16,
                                                  ),
                                                  label: const Text(
                                                    'إعادة الإرسال',
                                                  ),
                                                ),
                                              ),
                                            ] else if (outgoing &&
                                                item['is_deleted'] != true &&
                                                item['is_deleted'] != 1) ...[
                                              const SizedBox(height: 3),
                                              Text(
                                                item['delivery_status'] ==
                                                        'delivered'
                                                    ? 'أُرسل عبر تيليجرام'
                                                    : 'حالة الإرسال غير معروفة',
                                                style: Theme.of(
                                                  context,
                                                ).textTheme.labelSmall,
                                              ),
                                              if (_canManageMessages &&
                                                  item['telegram_message_id'] !=
                                                      null)
                                                Wrap(
                                                  spacing: 4,
                                                  children: [
                                                    TextButton.icon(
                                                      onPressed: () =>
                                                          _editMessage(item),
                                                      icon: const Icon(
                                                        Icons.edit_outlined,
                                                        size: 15,
                                                      ),
                                                      label: const Text(
                                                        'تعديل',
                                                      ),
                                                    ),
                                                    TextButton.icon(
                                                      onPressed: () =>
                                                          _deleteMessage(item),
                                                      icon: const Icon(
                                                        Icons.delete_outline,
                                                        size: 15,
                                                      ),
                                                      label: const Text('حذف'),
                                                    ),
                                                  ],
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
                          style: TextStyle(
                            color: Theme.of(context).colorScheme.error,
                          ),
                        ),
                      ),
                  ],
                ),
              ),
            ),
            _buildComposer(dialogWidth),
          ],
        ),
      ),
    );
  }
}

// ==================================================================
// 6) التصنيفات: محرر قائمة أنواع المخالفات (يقرأها البوت فوراً)
// ==================================================================
