import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';
import 'overview_page.dart' show fmt;

class AuditPage extends StatefulWidget {
  const AuditPage({super.key, required this.api});

  final AdminApi api;

  @override
  State<AuditPage> createState() => _AuditPageState();
}

class _AuditPageState extends State<AuditPage> {
  List<Map<String, dynamic>> _items = [];
  bool _loading = true;
  String? _err;
  Timer? _timer;
  DateTimeRange? _range;

  @override
  void initState() {
    super.initState();
    _load();
    _timer = Timer.periodic(const Duration(seconds: 20), (t) {
      if (!mounted) {
        t.cancel();
        return;
      }
      _load(silent: true);
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _load({bool silent = false}) async {
    if (!silent) {
      setState(() {
        _loading = true;
        _err = null;
      });
    }
    try {
      final range = _range;
      final d = await widget.api.get('/api/admin/audit', {
        'limit': '5000',
        if (range != null) 'start': _day(range.start),
        if (range != null) 'end': _day(range.end),
      });
      if (!mounted) return;
      setState(() {
        _items = (d['items'] as List)
            .map((e) => (e as Map).cast<String, dynamic>())
            .toList();
        _loading = false;
        _err = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _err = errMsg(e);
      });
    }
  }

  String _actorName(dynamic v) {
    if (v == null) return 'النظام';
    final n = v is int ? v : int.tryParse('$v');
    if (n == null) return '?';
    if (n == 0) return 'لوحة الإدارة';
    return 'ID $n';
  }

  static const Map<String, String> _actionNames = {
    'status_changed': 'تغيير الحالة',
    'type_changed': 'تغيير التصنيف',
    'note_changed': 'ملاحظة إدارية',
    'panel_reply': 'رد رسمي',
    'report_deleted': 'حذف بلاغ',
    'user_blocked': 'حجب مستخدم',
    'user_unblocked': 'رفع حجب',
    'automated_explicit_image_freeze': 'تجميد دائم آلي لصورة صريحة',
    'automated_sensitive_image_freeze': 'تجميد دائم آلي لمحتوى حساس',
    'user_message': 'رسالة مباشرة لمستخدم',
    'emergency_on': '🛑 إيقاف طارئ',
    'emergency_off': '🟢 استئناف',
    'emergency_auto': 'إيقاف تلقائي',
    'broadcast': '📢 تعميم',
    'templates_changed': 'تعديل القوالب',
    'vtypes_changed': 'تعديل التصنيفات',
    'panel_backup_download': 'تنزيل نسخة',
    'panel_backup_upload': 'رفع نسخة',
    'report': 'بلاغ من تلغرام',
  };

  String _actionName(String a) => _actionNames[a] ?? a;

  String _day(DateTime date) =>
      '${date.year.toString().padLeft(4, '0')}-'
      '${date.month.toString().padLeft(2, '0')}-'
      '${date.day.toString().padLeft(2, '0')}';

  Future<void> _chooseRange() async {
    final now = DateTime.now();
    final range = await showDateRangePicker(
      context: context,
      firstDate: DateTime(2000),
      lastDate: now,
      initialDateRange:
          _range ??
          DateTimeRange(start: now.subtract(const Duration(days: 6)), end: now),
      helpText: 'اختر فترة سجل التدقيق',
    );
    if (range == null || !mounted) return;
    setState(() => _range = range);
    await _load();
  }

  Future<void> _exportRange() async {
    final range = _range;
    if (range == null) {
      toast(context, 'اختر فترة زمنية أولاً', error: true);
      return;
    }
    try {
      await widget.api.download(
        '/api/admin/audit/export?start=${_day(range.start)}&end=${_day(range.end)}',
      );
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    }
  }

  Future<void> _cleanupRange() async {
    final range = _range;
    if (range == null) {
      toast(context, 'اختر فترة زمنية أولاً', error: true);
      return;
    }
    final start = _day(range.start);
    final end = _day(range.end);
    final confirmed = await confirmDialog(
      context,
      'تنظيف سجل التدقيق',
      'سيتم حذف عمليات الفترة من $start إلى $end نهائيًا.',
      okLabel: 'حذف السجل',
      danger: true,
    );
    if (!confirmed || !mounted) return;
    try {
      final result = await widget.api.post('/api/admin/audit/cleanup', {
        'start': start,
        'end': end,
      });
      if (!mounted) return;
      toast(context, 'حُذف ${result['deleted'] ?? 0} سجل');
      await _load();
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: Row(
            children: [
              Icon(
                Icons.history,
                size: 18,
                color: Theme.of(context).colorScheme.onSurfaceVariant,
              ),
              const SizedBox(width: 8),
              Text(
                '${_items.length} عملية — '
                '${_range == null ? 'أحدث السجلات' : '${_day(_range!.start)} إلى ${_day(_range!.end)}'}',
                style: TextStyle(
                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                  fontSize: 13,
                ),
              ),
              const Spacer(),
              IconButton(
                tooltip: 'تصفية حسب التاريخ',
                onPressed: _chooseRange,
                icon: const Icon(Icons.date_range_outlined),
              ),
              IconButton(
                tooltip: 'تصدير الفترة المحددة',
                onPressed: _range == null ? null : _exportRange,
                icon: const Icon(Icons.download_outlined),
              ),
              IconButton(
                tooltip: 'تنظيف الفترة المحددة',
                onPressed: _range == null ? null : _cleanupRange,
                icon: const Icon(Icons.delete_sweep_outlined),
              ),
              if (_range != null)
                IconButton(
                  tooltip: 'إلغاء التصفية',
                  onPressed: () {
                    setState(() => _range = null);
                    _load();
                  },
                  icon: const Icon(Icons.filter_alt_off_outlined),
                ),
              IconButton(
                tooltip: 'تحديث',
                onPressed: _load,
                icon: const Icon(Icons.refresh),
              ),
            ],
          ),
        ),
        const Divider(height: 1),
        Expanded(child: _body(context)),
      ],
    );
  }

  Widget _body(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_err != null) return ErrorView(_err!, () => _load());
    if (_items.isEmpty) return const EmptyView('لا عمليات مسجلة بعد');
    return SingleChildScrollView(
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: DataTable(
          showCheckboxColumn: false,
          headingRowColor: WidgetStatePropertyAll(
            Theme.of(context).colorScheme.surfaceContainerHighest,
          ),
          columns: const [
            DataColumn(label: Text('الوقت')),
            DataColumn(label: Text('المنفِّذ')),
            DataColumn(label: Text('العملية')),
            DataColumn(label: Text('الهدف')),
            DataColumn(label: Text('التفاصيل')),
          ],
          rows: [
            for (final i in _items)
              DataRow(
                cells: [
                  DataCell(
                    SelectableText(
                      fmt(i['ts'] as String?, chars: 19),
                      textDirection: TextDirection.ltr,
                      style: const TextStyle(fontSize: 12),
                    ),
                  ),
                  DataCell(
                    Text(
                      (i['action'] ?? '') ==
                                  'automated_explicit_image_freeze' ||
                              (i['action'] ?? '') ==
                                  'automated_sensitive_image_freeze'
                          ? 'فحص الصور الآلي'
                          : _actorName(i['actor_id'] ?? i['actor']),
                    ),
                  ),
                  DataCell(
                    Text(
                      _actionName((i['action'] ?? '-') as String),
                      style: const TextStyle(fontWeight: FontWeight.w600),
                    ),
                  ),
                  DataCell(SelectableText('${i['target'] ?? '—'}')),
                  DataCell(
                    SizedBox(
                      width: 340,
                      child: Text(
                        '${i['details'] ?? '—'}',
                        style: const TextStyle(fontSize: 12),
                      ),
                    ),
                  ),
                ],
              ),
          ],
        ),
      ),
    );
  }
}

// ==================================================================
// 8) المُقيَّدون: كل المحجوبين مع السبب وزر رفع الحجب + زر تواصل مباشر
// ==================================================================
