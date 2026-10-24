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
      final d = await widget.api.get('/api/admin/audit', {'limit': '120'});
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

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: Row(
            children: [
              const Icon(Icons.history, size: 18, color: Colors.white38),
              const SizedBox(width: 8),
              Text(
                '${_items.length} عملية مسجلة — تحديث تلقائي',
                style: const TextStyle(color: Colors.white38, fontSize: 13),
              ),
              const Spacer(),
              IconButton(
                tooltip: 'تحديث',
                onPressed: _load,
                icon: const Icon(Icons.refresh),
              ),
            ],
          ),
        ),
        const Divider(height: 1),
        Expanded(child: _body()),
      ],
    );
  }

  Widget _body() {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_err != null) return ErrorView(_err!, () => _load());
    if (_items.isEmpty) return const EmptyView('لا عمليات مسجلة بعد');
    return SingleChildScrollView(
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: DataTable(
          showCheckboxColumn: false,
          headingRowColor: WidgetStatePropertyAll(
            const Color(0xFF12221E).withOpacity(0.8),
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
                  DataCell(Text(_actorName(i['actor_id'] ?? i['actor']))),
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
