import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';
import 'shared_dialogs.dart';

class BlockedPage extends StatefulWidget {
  const BlockedPage({
    super.key,
    required this.api,
    this.initialUserId,
    this.onFocusHandled,
  });

  final AdminApi api;
  final int? initialUserId;
  final VoidCallback? onFocusHandled;

  @override
  State<BlockedPage> createState() => _BlockedPageState();
}

class _BlockedPageState extends State<BlockedPage> {
  static const int _pageSize = 25;

  List<Map<String, dynamic>> _items = [];
  int _page = 0;
  int _total = 0;
  int? _busyUserId;
  bool _loading = true;
  String? _err;

  @override
  void initState() {
    super.initState();
    _load().whenComplete(() => widget.onFocusHandled?.call());
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _err = null;
    });
    try {
      final data = await widget.api.get('/api/admin/blocked-users', {
        'page': '$_page',
        'size': '$_pageSize',
        if (widget.initialUserId != null) 'user_id': '${widget.initialUserId}',
      });
      if (!mounted) return;
      final total = data['total'] as int? ?? 0;
      final lastPage = total == 0 ? 0 : (total - 1) ~/ _pageSize;
      if (_page > lastPage) {
        setState(() => _page = lastPage);
        await _load();
        return;
      }
      setState(() {
        _items = (data['items'] as List? ?? const [])
            .map((item) => Map<String, dynamic>.from(item as Map))
            .toList();
        _total = total;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _err = errMsg(e);
      });
    }
  }

  Future<void> _unblock(Map<String, dynamic> user) async {
    final uid = user['user_id'] as int;
    final until = _untilLabel(user['frozen_until']?.toString());
    final confirmed = await confirmDialog(
      context,
      'رفع الحجب',
      'رفع الحجب عن المستخدم $uid؟\nحالة الحجب الحالية: $until',
      okLabel: 'رفع الحجب',
    );
    if (!confirmed || !mounted) return;

    setState(() => _busyUserId = uid);
    try {
      final result = await widget.api.post('/api/admin/users/$uid/unblock', {});
      if (!mounted) return;
      toast(
        context,
        result['notified'] == true
            ? 'تم رفع الحجب وإشعار المستخدم $uid'
            : 'تم رفع الحجب، لكن تعذّر إرسال إشعار للمستخدم $uid',
        error: result['notified'] != true,
      );
      await _load();
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    } finally {
      if (mounted) setState(() => _busyUserId = null);
    }
  }

  Future<void> _messageUser(Map<String, dynamic> user) async {
    final uid = user['user_id'] as int;
    final text = await requiredTextDialog(
      context,
      title: 'رسالة مباشرة للمستخدم',
      hint: 'اكتب رسالة سترسل إليه عبر البوت…',
      maxLines: 4,
    );
    if (text == null || !mounted) return;
    final confirmed = await confirmDialog(
      context,
      'تأكيد الإرسال',
      'إرسال الرسالة مباشرة إلى المستخدم $uid عبر Telegram؟',
      okLabel: 'إرسال',
    );
    if (!confirmed || !mounted) return;

    setState(() => _busyUserId = uid);
    try {
      final result = await widget.api.post('/api/admin/users/$uid/message', {
        'text': text,
      });
      if (!mounted) return;
      if (result['sent'] != true) {
        toast(context, 'لم يؤكد الخادم تسليم الرسالة', error: true);
      } else {
        toast(context, 'أُرسلت الرسالة للمستخدم $uid');
      }
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    } finally {
      if (mounted) setState(() => _busyUserId = null);
    }
  }

  String _untilLabel(String? value) {
    if (value == null || value.isEmpty) return 'غير محدد';
    if (value.startsWith('9999-')) return 'دائم';
    return value.length > 19 ? value.substring(0, 19) : value;
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_err != null) return ErrorView(_err!, _load);
    if (_total == 0) {
      return const EmptyView('لا يوجد مستخدمون محجوبون حاليًا');
    }

    final pages = (_total / _pageSize).ceil();
    return Column(
      children: [
        Expanded(
          child: ListView(
            padding: const EdgeInsets.all(16),
            children: [
              SectionCard(
                'المُقيَّدون',
                icon: Icons.block_outlined,
                trailing: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      '$_total حجب سارٍ',
                      style: TextStyle(
                        color: Theme.of(context).colorScheme.onSurfaceVariant,
                      ),
                    ),
                    IconButton(
                      tooltip: 'تحديث القائمة',
                      onPressed: _load,
                      icon: const Icon(Icons.refresh),
                    ),
                  ],
                ),
                child: Column(
                  children: [
                    for (var i = 0; i < _items.length; i++) ...[
                      if (i > 0) const Divider(height: 1),
                      _blockedUser(_items[i]),
                    ],
                  ],
                ),
              ),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 8),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              IconButton(
                tooltip: 'الصفحة التالية',
                onPressed: _page + 1 >= pages
                    ? null
                    : () {
                        setState(() => _page++);
                        _load();
                      },
                icon: const Icon(Icons.chevron_right),
              ),
              Text(
                'الصفحة ${_page + 1} من $pages',
                style: TextStyle(
                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                ),
              ),
              IconButton(
                tooltip: 'الصفحة السابقة',
                onPressed: _page == 0
                    ? null
                    : () {
                        setState(() => _page--);
                        _load();
                      },
                icon: const Icon(Icons.chevron_left),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _blockedUser(Map<String, dynamic> user) {
    final uid = user['user_id'] as int;
    final busy = _busyUserId == uid;
    final reportId = (user['report_id'] as num?)?.toInt();
    final contactValue = user['contact_enabled'];
    final contactEnabled =
        contactValue == null ||
        contactValue == true ||
        contactValue == 1 ||
        contactValue == '1';
    final fullName = (user['full_name'] as String?)?.trim() ?? '';
    final username = (user['username'] as String?)?.trim() ?? '';
    final reason = (user['reason'] as String?)?.trim() ?? '';
    final until = _untilLabel(user['frozen_until']?.toString());
    final hasEvidencePhoto =
        user['has_evidence_photo'] == true || user['has_evidence_photo'] == 1;
    final photoPath = hasEvidencePhoto
        ? '/api/admin/blocked-users/$uid/photo'
        : reportId == null
        ? null
        : '/api/admin/photo/$reportId';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        ListTile(
          contentPadding: const EdgeInsets.symmetric(
            horizontal: 4,
            vertical: 6,
          ),
          leading: const CircleAvatar(
            backgroundColor: Color(0xFF351D20),
            child: Icon(
              Icons.person_off_outlined,
              size: 20,
              color: Color(0xFFE06C75),
            ),
          ),
          title: Text(
            fullName.isEmpty ? 'مستخدم Telegram' : fullName,
            style: const TextStyle(fontWeight: FontWeight.w700),
          ),
          subtitle: Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Wrap(
              spacing: 12,
              runSpacing: 6,
              children: [
                SelectableText('ID $uid', textDirection: TextDirection.ltr),
                if (username.isNotEmpty)
                  Text('@$username', textDirection: TextDirection.ltr),
                Text('المدة: $until'),
                Text('السبب: ${reason.isEmpty ? 'غير مسجل' : reason}'),
              ],
            ),
          ),
          trailing: Wrap(
            spacing: 2,
            children: [
              IconButton(
                tooltip: 'إرسال رسالة عبر البوت',
                onPressed: busy || !contactEnabled
                    ? null
                    : () => _messageUser(user),
                icon: const Icon(Icons.chat_bubble_outline),
              ),
              IconButton(
                tooltip: 'رفع الحجب',
                onPressed: busy ? null : () => _unblock(user),
                icon: busy
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.lock_open_outlined),
              ),
            ],
          ),
        ),
        SwitchListTile.adaptive(
          dense: true,
          title: const Text('السماح بالتواصل المباشر'),
          value: contactEnabled,
          onChanged: busy ? null : (enabled) => _setContact(uid, enabled),
        ),
        _BlockedEvidence(
          api: widget.api,
          photoPath: photoPath,
          reportPath: '/api/admin/blocked-users/$uid/report',
          hasSensitiveEvidence: hasEvidencePhoto,
        ),
        const SizedBox(height: 8),
      ],
    );
  }

  Future<void> _setContact(int userId, bool enabled) async {
    setState(() => _busyUserId = userId);
    try {
      final result = await widget.api.put('/api/admin/users/$userId/contact', {
        'enabled': enabled,
      });
      if (!mounted) return;
      toast(
        context,
        result['notified'] == true
            ? 'تم تحديث إعداد التواصل وإشعار المستخدم'
            : 'تم تحديث الإعداد، لكن تعذّر إشعار المستخدم',
        error: result['notified'] != true,
      );
      await _load();
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    } finally {
      if (mounted) setState(() => _busyUserId = null);
    }
  }
}

class _BlockedEvidence extends StatefulWidget {
  const _BlockedEvidence({
    required this.api,
    required this.photoPath,
    required this.reportPath,
    required this.hasSensitiveEvidence,
  });

  final AdminApi api;
  final String? photoPath;
  final String reportPath;
  final bool hasSensitiveEvidence;

  @override
  State<_BlockedEvidence> createState() => _BlockedEvidenceState();
}

class _BlockedEvidenceState extends State<_BlockedEvidence> {
  bool _revealed = false;
  Future<Uint8List>? _photo;

  @override
  void initState() {
    super.initState();
    final path = widget.photoPath;
    if (path != null) _photo = widget.api.getBytes(path);
  }

  Future<void> _download(String path) async {
    try {
      await widget.api.download(path);
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    }
  }

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsetsDirectional.only(start: 56, end: 8, bottom: 8),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (widget.photoPath != null) ...[
          Text(
            widget.hasSensitiveEvidence
                ? 'صورة دليل الحظر — مموهة لحماية المحتوى'
                : 'صورة آخر بلاغ — مموهة لحماية المحتوى',
            style: const TextStyle(fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 8),
          SizedBox(
            height: 180,
            child: FutureBuilder<Uint8List>(
              future: _photo,
              builder: (context, snapshot) {
                if (snapshot.hasError) {
                  return const Center(child: Text('تعذّر تحميل الصورة'));
                }
                if (!snapshot.hasData) {
                  return const Center(child: CircularProgressIndicator());
                }
                final image = Image.memory(
                  snapshot.data!,
                  fit: BoxFit.contain,
                  height: 180,
                );
                return Stack(
                  alignment: Alignment.center,
                  children: [
                    if (_revealed)
                      image
                    else
                      ImageFiltered(
                        imageFilter: ui.ImageFilter.blur(
                          sigmaX: 14,
                          sigmaY: 14,
                        ),
                        child: image,
                      ),
                    if (!_revealed)
                      FilledButton.tonalIcon(
                        onPressed: () => setState(() => _revealed = true),
                        icon: const Icon(Icons.visibility_outlined),
                        label: const Text('إظهار الصورة'),
                      ),
                  ],
                );
              },
            ),
          ),
        ],
        Wrap(
          spacing: 8,
          children: [
            if (widget.photoPath != null)
              TextButton.icon(
                onPressed: () => _download('${widget.photoPath}?download=true'),
                icon: const Icon(Icons.image_outlined),
                label: const Text('تنزيل الصورة'),
              ),
            TextButton.icon(
              onPressed: () => _download(widget.reportPath),
              icon: const Icon(Icons.description_outlined),
              label: const Text('تنزيل تقرير الحظر'),
            ),
          ],
        ),
      ],
    ),
  );
}
