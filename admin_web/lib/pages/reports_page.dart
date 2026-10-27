import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../api.dart';
import '../design_tokens.dart';
import '../widgets.dart';
import 'overview_page.dart' show fmt, kStatusOrder, kStatusStyles;
import 'shared_dialogs.dart';
import 'user_dossier_dialog.dart';

String priorityLabel(String value) => switch (value) {
  'low' => 'منخفضة',
  'high' => 'عالية',
  'urgent' => 'عاجلة',
  _ => 'عادية',
};

class ReportsPage extends StatefulWidget {
  const ReportsPage({
    super.key,
    required this.api,
    required this.onOpenConversation,
    this.focusReportId,
    this.onFocusHandled,
  });

  final AdminApi api;
  final ValueChanged<int> onOpenConversation;
  final int? focusReportId;
  final VoidCallback? onFocusHandled;

  @override
  State<ReportsPage> createState() => _ReportsPageState();
}

class _ReportsPageState extends State<ReportsPage> {
  List<Map<String, dynamic>> _items = [];
  List<String> _types = [];
  List<int> _adminIds = [];
  int _total = 0;
  int _page = 0;
  static const int _size = 25;
  String? _status;
  String? _type;
  String? _priorityFilter;
  int? _assignedFilter;
  bool _overdueOnly = false;
  bool _unassignedOnly = false;
  final TextEditingController _q = TextEditingController();
  bool _loading = true;
  String? _err;

  @override
  void initState() {
    super.initState();
    _bootstrap();
  }

  Future<void> _bootstrap() async {
    // القائمة الفعلية (بعد أي تعديل من لوحة الإدارة) — وإلا الإعداد الافتراضي
    try {
      final v = await widget.api.get('/api/admin/vtypes');
      if (!mounted) return;
      setState(() => _types = List<String>.from(v['items'] ?? <dynamic>[]));
    } catch (_) {
      try {
        final meta = await widget.api.get('/api/admin/meta');
        if (!mounted) return;
        setState(
          () => _types = List<String>.from(meta['vtypes'] ?? <dynamic>[]),
        );
      } catch (_) {
        // قائمة التصنيفات ليست حاكمة
      }
    }
    try {
      final meta = await widget.api.get('/api/admin/meta');
      if (mounted) {
        setState(() => _adminIds = List<int>.from(meta['admin_ids'] ?? []));
      }
    } on ApiException catch (error) {
      if (error.status != 404 && mounted) {
        toast(context, errMsg(error), error: true);
      }
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    }
    await _load();
    final focusReportId = widget.focusReportId;
    if (focusReportId != null && mounted) {
      try {
        final result = await widget.api.get(
          '/api/admin/reports/$focusReportId',
        );
        final item = (result['item'] as Map).cast<String, dynamic>();
        if (mounted) await _detailDialog(context, item);
      } catch (error) {
        if (mounted) toast(context, errMsg(error), error: true);
      } finally {
        widget.onFocusHandled?.call();
      }
    }
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _err = null;
    });
    final q = <String, String>{
      'page': '$_page',
      'size': '$_size',
      if (_status != null) 'status': _status!,
      if (_type != null) 'type': _type!,
      if (_priorityFilter != null) 'priority': _priorityFilter!,
      if (_assignedFilter != null) 'assigned_to': '$_assignedFilter',
      if (_unassignedOnly) 'unassigned': 'true',
      if (_overdueOnly) 'overdue': 'true',
      if (_q.text.trim().isNotEmpty) 'q': _q.text.trim(),
    };
    try {
      final data = await widget.api.get('/api/admin/reports', q);
      if (!mounted) return;
      final items = (data['items'] ?? <dynamic>[]) as List;
      setState(() {
        _items = items.map((e) => (e as Map).cast<String, dynamic>()).toList();
        _total = (data['total'] ?? 0) as int;
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

  Future<void> _downloadPhoto(int reportId) async {
    try {
      await widget.api.download('/api/admin/photo/$reportId?download=true');
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    }
  }

  void _openConversation(Map<String, dynamic> report) {
    final userId = int.tryParse('${report['user_id'] ?? ''}');
    if (userId == null) {
      toast(context, 'معرّف المستخدم غير متاح', error: true);
      return;
    }
    widget.onOpenConversation(userId);
  }

  void _shiftPage(int d) {
    setState(() => _page = (_page + d).clamp(0, 100000));
    _load();
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(
            AdminTokens.pageInset,
            12,
            AdminTokens.pageInset,
            8,
          ),
          child: SectionCard(
            'تصفية البلاغات',
            subtitle: 'ابحث وضيّق النتائج حسب الحالة والأولوية والتكليف',
            icon: Icons.filter_alt_outlined,
            trailing: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                StateBadge(
                  '$_total نتيجة',
                  color: Theme.of(context).colorScheme.primary,
                ),
                IconButton(
                  tooltip: 'تحديث النتائج',
                  onPressed: _load,
                  icon: const Icon(Icons.refresh),
                ),
              ],
            ),
            child: LayoutBuilder(
              builder: (context, constraints) {
                final searchWidth = constraints.maxWidth < 560
                    ? constraints.maxWidth
                    : 260.0;
                return Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    DropdownButton<String?>(
                      value: _status,
                      hint: const Text('كل الحالات'),
                      underline: const SizedBox(),
                      items: [
                        const DropdownMenuItem<String?>(
                          value: null,
                          child: Text('كل الحالات'),
                        ),
                        for (final s in kStatusOrder)
                          DropdownMenuItem<String?>(value: s, child: Text(s)),
                      ],
                      onChanged: (v) {
                        setState(() {
                          _status = v;
                          _page = 0;
                        });
                        _load();
                      },
                    ),
                    DropdownButton<int?>(
                      value: _assignedFilter,
                      hint: const Text('كل المشرفين'),
                      underline: const SizedBox(),
                      items: [
                        const DropdownMenuItem<int?>(
                          value: null,
                          child: Text('كل المشرفين'),
                        ),
                        for (final id in _adminIds)
                          DropdownMenuItem<int?>(
                            value: id,
                            child: Text('مشرف $id'),
                          ),
                      ],
                      onChanged: (value) {
                        setState(() {
                          _assignedFilter = value;
                          if (value != null) _unassignedOnly = false;
                          _page = 0;
                        });
                        _load();
                      },
                    ),
                    DropdownButton<String?>(
                      value: _priorityFilter,
                      hint: const Text('كل الأولويات'),
                      underline: const SizedBox(),
                      items: const [
                        DropdownMenuItem<String?>(
                          value: null,
                          child: Text('كل الأولويات'),
                        ),
                        DropdownMenuItem<String?>(
                          value: 'urgent',
                          child: Text('عاجلة'),
                        ),
                        DropdownMenuItem<String?>(
                          value: 'high',
                          child: Text('عالية'),
                        ),
                        DropdownMenuItem<String?>(
                          value: 'normal',
                          child: Text('عادية'),
                        ),
                        DropdownMenuItem<String?>(
                          value: 'low',
                          child: Text('منخفضة'),
                        ),
                      ],
                      onChanged: (value) {
                        setState(() {
                          _priorityFilter = value;
                          _page = 0;
                        });
                        _load();
                      },
                    ),
                    FilterChip(
                      label: const Text('متأخرة'),
                      selected: _overdueOnly,
                      onSelected: (value) {
                        setState(() {
                          _overdueOnly = value;
                          _page = 0;
                        });
                        _load();
                      },
                    ),
                    FilterChip(
                      label: const Text('غير مسندة'),
                      selected: _unassignedOnly,
                      onSelected: (value) {
                        setState(() {
                          _unassignedOnly = value;
                          if (value) _assignedFilter = null;
                          _page = 0;
                        });
                        _load();
                      },
                    ),
                    DropdownButton<String?>(
                      value: _type,
                      hint: const Text('كل التصنيفات'),
                      underline: const SizedBox(),
                      items: [
                        const DropdownMenuItem<String?>(
                          value: null,
                          child: Text('كل التصنيفات'),
                        ),
                        for (final t in _types)
                          DropdownMenuItem<String?>(value: t, child: Text(t)),
                      ],
                      onChanged: (v) {
                        setState(() {
                          _type = v;
                          _page = 0;
                        });
                        _load();
                      },
                    ),
                    SizedBox(
                      width: searchWidth,
                      child: TextField(
                        controller: _q,
                        decoration: const InputDecoration(
                          prefixIcon: Icon(Icons.search, size: 20),
                          hintText: 'بحث: رقم/اسم/وصف/رابط/معرف',
                          isDense: true,
                          border: OutlineInputBorder(),
                        ),
                        onSubmitted: (_) {
                          setState(() => _page = 0);
                          _load();
                        },
                      ),
                    ),
                    FilledButton.tonalIcon(
                      onPressed: () {
                        setState(() => _page = 0);
                        _load();
                      },
                      icon: const Icon(Icons.search),
                      label: const Text('بحث'),
                    ),
                  ],
                );
              },
            ),
          ),
        ),
        Expanded(child: _body(context)),
        Padding(
          padding: const EdgeInsets.all(10),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              IconButton(
                onPressed: _page == 0 ? null : () => _shiftPage(-1),
                tooltip: 'الصفحة السابقة',
                icon: const Icon(Icons.chevron_right),
              ),
              Flexible(
                child: Text(
                  'صفحة ${_page + 1} من ${(_total / _size).ceil().clamp(1, 100000)} · $_total بلاغ',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  textAlign: TextAlign.center,
                  style: TextStyle(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
                ),
              ),
              IconButton(
                onPressed: (_page + 1) * _size >= _total
                    ? null
                    : () => _shiftPage(1),
                tooltip: 'الصفحة التالية',
                icon: const Icon(Icons.chevron_left),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _body(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_err != null) return ErrorView(_err!, _load);
    if (_items.isEmpty) return const EmptyView('لا بلاغات مطابقة');
    return LayoutBuilder(
      builder: (context, constraints) {
        if (constraints.maxWidth < 760) {
          return ListView.separated(
            padding: const EdgeInsets.all(12),
            itemCount: _items.length,
            separatorBuilder: (_, _) => const SizedBox(height: 10),
            itemBuilder: (context, index) =>
                _reportCard(context, _items[index]),
          );
        }
        return SingleChildScrollView(
          child: SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: DataTable(
              showCheckboxColumn: false,
              headingRowColor: WidgetStatePropertyAll(
                Theme.of(context).colorScheme.surfaceContainerHighest,
              ),
              columns: const [
                DataColumn(label: Text('الرقم')),
                DataColumn(label: Text('الحالة')),
                DataColumn(label: Text('الاسم')),
                DataColumn(label: Text('المعرف')),
                DataColumn(label: Text('التصنيف')),
                DataColumn(label: Text('الأولوية')),
                DataColumn(label: Text('نشاط المستخدم')),
                DataColumn(label: Text('التاريخ')),
                DataColumn(label: Text('إجراءات')),
              ],
              rows: [for (final r in _items) _row(context, r)],
            ),
          ),
        );
      },
    );
  }

  Widget _reportCard(BuildContext context, Map<String, dynamic> report) {
    final theme = Theme.of(context);
    final status = report['status']?.toString() ?? '-';
    final style = kStatusStyles[status];
    final reportId = int.tryParse('${report['id'] ?? ''}');
    final userId = int.tryParse('${report['user_id'] ?? ''}');
    final priority = '${report['priority'] ?? 'normal'}';
    final hasPhoto =
        report['has_photo'] == true ||
        (report['photo_file_id'] ?? '').toString().isNotEmpty;

    return Card(
      clipBehavior: Clip.antiAlias,
      color: status == 'جديد'
          ? theme.colorScheme.surfaceContainerHighest
          : theme.cardColor,
      child: InkWell(
        onTap: () => _detailDialog(context, report),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: SelectableText(
                      '${report['seq'] ?? '-'}',
                      textDirection: TextDirection.ltr,
                      style: theme.textTheme.titleSmall?.copyWith(
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                  ),
                  StateBadge(status, color: style?.$1 ?? Colors.grey),
                ],
              ),
              const SizedBox(height: 10),
              Row(
                children: [
                  Expanded(
                    child: Text(
                      '${report['full_name'] ?? '-'}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  UserActivityIndicator(
                    lastSeen: report['user_last_at'] as String?,
                  ),
                ],
              ),
              const SizedBox(height: 4),
              Text(
                'معرّف المستخدم ${report['user_id'] ?? '-'}'
                '  ·  ${fmt((report['created_at'] ?? '') as String)}',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                textDirection: TextDirection.rtl,
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: 12),
              Wrap(
                spacing: 7,
                runSpacing: 7,
                children: [
                  StateBadge(
                    '${report['violation_type'] ?? 'بلا تصنيف'}',
                    color: theme.colorScheme.primary,
                  ),
                  StateBadge(
                    'أولوية ${priorityLabel(priority)}',
                    color: _priorityColor(theme, priority),
                  ),
                ],
              ),
              const SizedBox(height: 6),
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: Wrap(
                  spacing: 4,
                  children: [
                    if (hasPhoto)
                      IconButton(
                        tooltip: 'تنزيل صورة البلاغ',
                        onPressed: reportId == null
                            ? null
                            : () => _downloadPhoto(reportId),
                        icon: const Icon(Icons.download_outlined),
                      ),
                    IconButton(
                      tooltip: 'فتح محادثة مباشرة مع المستخدم',
                      onPressed: userId == null
                          ? null
                          : () => _openConversation(report),
                      icon: const Icon(Icons.chat_outlined),
                    ),
                    TextButton.icon(
                      onPressed: () => _detailDialog(context, report),
                      icon: const Icon(Icons.open_in_new, size: 17),
                      label: const Text('تفاصيل البلاغ'),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Color _priorityColor(ThemeData theme, String priority) => switch (priority) {
    'urgent' => const Color(0xFFFF7C83),
    'high' => const Color(0xFFFFC857),
    'low' => theme.colorScheme.onSurfaceVariant,
    _ => theme.colorScheme.primary,
  };

  DataRow _row(BuildContext context, Map<String, dynamic> r) {
    final String status = (r['status'] ?? '-') as String;
    final style = kStatusStyles[status];
    final reportId = int.tryParse('${r['id'] ?? ''}');
    final userId = int.tryParse('${r['user_id'] ?? ''}');
    return DataRow(
      color: WidgetStatePropertyAll(
        status == 'جديد'
            ? Theme.of(context).colorScheme.surfaceContainerHighest
            : Colors.transparent,
      ),
      cells: [
        DataCell(
          SelectableText(
            '${r['seq'] ?? '-'}',
            textDirection: TextDirection.ltr,
            style: const TextStyle(fontWeight: FontWeight.w600),
          ),
        ),
        DataCell(
          Row(
            children: [
              Icon(
                style?.$2 ?? Icons.help_outline,
                size: 15,
                color: style?.$1 ?? Colors.grey,
              ),
              const SizedBox(width: 6),
              Text(status, style: TextStyle(color: style?.$1 ?? Colors.grey)),
            ],
          ),
        ),
        DataCell(Text('${r['full_name'] ?? '-'}')),
        DataCell(
          SelectableText(
            '${r['user_id'] ?? '-'}',
            textDirection: TextDirection.ltr,
          ),
        ),
        DataCell(Text('${r['violation_type'] ?? '—'}')),
        DataCell(Text(priorityLabel('${r['priority'] ?? 'normal'}'))),
        DataCell(UserActivityIndicator(lastSeen: r['user_last_at'] as String?)),
        DataCell(
          Text(
            fmt((r['created_at'] ?? '') as String),
            textDirection: TextDirection.ltr,
            style: const TextStyle(fontSize: 12),
          ),
        ),
        DataCell(
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (r['has_photo'] == true ||
                  (r['photo_file_id'] ?? '').toString().isNotEmpty)
                IconButton(
                  tooltip: 'تنزيل صورة البلاغ',
                  onPressed: reportId == null
                      ? null
                      : () => _downloadPhoto(reportId),
                  icon: const Icon(Icons.download, size: 19),
                ),
              IconButton(
                tooltip: 'فتح محادثة مباشرة مع المستخدم',
                onPressed: userId == null ? null : () => _openConversation(r),
                icon: const Icon(Icons.chat_outlined, size: 19),
              ),
            ],
          ),
        ),
      ],
      onSelectChanged: (_) => _detailDialog(context, r),
    );
  }

  Future<void> _detailDialog(
    BuildContext context,
    Map<String, dynamic> r,
  ) async {
    final rid = (r['id'] ?? 0) as int;
    await showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (ctx) => LayoutBuilder(
        builder: (context, constraints) {
          final width = constraints.maxWidth < 964
              ? constraints.maxWidth - 24
              : 940.0;
          final height = constraints.maxHeight < 704
              ? constraints.maxHeight - 24
              : 680.0;
          return Dialog(
            insetPadding: const EdgeInsets.all(12),
            child: SizedBox(
              width: width,
              height: height,
              child: _ReportDetail(
                api: widget.api,
                rid: rid,
                types: _types,
                onOpenConversation: widget.onOpenConversation,
                onChanged: _load,
              ),
            ),
          );
        },
      ),
    );
  }
}

// ==================================================================
// تفصيل البلاغ + كامل الإجراءات
// ==================================================================
class _ReportDetail extends StatefulWidget {
  const _ReportDetail({
    required this.api,
    required this.rid,
    required this.types,
    required this.onOpenConversation,
    required this.onChanged,
  });

  final AdminApi api;
  final int rid;
  final List<String> types;
  final ValueChanged<int> onOpenConversation;
  final VoidCallback onChanged;

  @override
  State<_ReportDetail> createState() => _ReportDetailState();
}

class _ReportDetailState extends State<_ReportDetail> {
  Map<String, dynamic>? _r;
  Future<Uint8List>? _photoFuture;
  List<String> _templates = [];
  int _bust = DateTime.now().millisecondsSinceEpoch;
  final TextEditingController _reply = TextEditingController();
  final TextEditingController _note = TextEditingController();
  final TextEditingController _followup = TextEditingController();
  List<int> _adminIds = [];
  int? _assignedTo;
  String _priority = 'normal';
  DateTime? _dueAt;
  String? _typeSel;

  @override
  void initState() {
    super.initState();
    _fetch();
  }

  Future<Uint8List> _loadPhoto() =>
      widget.api.getBytes('/api/admin/photo/${widget.rid}?ts=$_bust');

  void _retryPhoto() {
    setState(() {
      _bust = DateTime.now().millisecondsSinceEpoch;
      _photoFuture = _loadPhoto();
    });
  }

  Future<void> _fetch() async {
    Map<String, dynamic> item = <String, dynamic>{};
    try {
      final d = await widget.api.get('/api/admin/reports/${widget.rid}');
      item = (d['item'] ?? <String, dynamic>{}) as Map<String, dynamic>;
    } catch (e) {
      if (!mounted) return;
      setState(() => _r = _r ?? <String, dynamic>{});
      toast(context, errMsg(e), error: true);
      return;
    }
    List<String> tpl = [];
    try {
      final t = await widget.api.get('/api/admin/templates');
      tpl = List<String>.from(t['items'] ?? <dynamic>[]);
    } catch (_) {
      // القوالب إضافة اختيارية
    }
    List<int> admins = [];
    try {
      final meta = await widget.api.get('/api/admin/meta');
      admins = List<int>.from(meta['admin_ids'] ?? <dynamic>[]);
    } catch (_) {
      // المشرفون غير متاحين في مزود API أقدم.
    }
    if (!mounted) return;
    setState(() {
      _r = item;
      _photoFuture =
          item['has_photo'] == true ||
              ((item['photo_file_id'] ?? '') as String).isNotEmpty
          ? _loadPhoto()
          : null;
      _templates = tpl;
      _adminIds = admins;
      _typeSel = (item['violation_type'] ?? '') as String?;
      _note.text = (item['admin_note'] ?? '') as String? ?? '';
      _assignedTo = int.tryParse('${item['assigned_to'] ?? ''}');
      _priority = (item['priority'] ?? 'normal') as String;
      _dueAt = DateTime.tryParse('${item['due_at'] ?? ''}');
    });
  }

  @override
  Widget build(BuildContext context) {
    final r = _r;
    if (r == null || r.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }
    final status = (r['status'] ?? '-') as String;
    final userId = int.tryParse('${r['user_id'] ?? ''}');
    return Column(
      children: [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
          decoration: BoxDecoration(
            color: Theme.of(context).colorScheme.surfaceContainerHighest,
            borderRadius: BorderRadius.vertical(top: Radius.circular(12)),
          ),
          child: Row(
            children: [
              Expanded(
                child: Text(
                  'تفصيل البلاغ ${r['seq'] ?? '-'}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontWeight: FontWeight.w700,
                    fontSize: 15,
                  ),
                ),
              ),
              IconButton(
                tooltip: 'فتح بطاقة الكشف للمبلّغ',
                onPressed: userId == null
                    ? null
                    : () => showUserDossier(context, widget.api, userId),
                icon: const Icon(Icons.manage_search_outlined, size: 20),
              ),
              IconButton(
                tooltip: 'فتح محادثة مباشرة مع المستخدم',
                onPressed: userId == null
                    ? null
                    : () => widget.onOpenConversation(userId),
                icon: const Icon(Icons.chat_outlined, size: 20),
              ),
              IconButton(
                tooltip: 'تحديث',
                onPressed: () {
                  _bust = DateTime.now().millisecondsSinceEpoch;
                  _fetch();
                },
                icon: const Icon(Icons.refresh, size: 20),
              ),
              IconButton(
                tooltip: 'إغلاق',
                onPressed: () => Navigator.of(context).pop(),
                icon: const Icon(Icons.close, size: 20),
              ),
            ],
          ),
        ),
        Expanded(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(14),
            child: LayoutBuilder(
              builder: (context, constraints) {
                final compact = constraints.maxWidth < 700;
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    if (compact) ...[
                      Align(
                        alignment: AlignmentDirectional.centerStart,
                        child: _photo(
                          context,
                          r,
                          availableWidth: constraints.maxWidth,
                        ),
                      ),
                      const SizedBox(height: 14),
                      _info(context, r),
                    ] else
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          _photo(
                            context,
                            r,
                            availableWidth: constraints.maxWidth,
                          ),
                          const SizedBox(width: 14),
                          Expanded(child: _info(context, r)),
                        ],
                      ),
                    const SizedBox(height: 12),
                    _actions(context, r, status),
                  ],
                );
              },
            ),
          ),
        ),
      ],
    );
  }

  Widget _photo(
    BuildContext context,
    Map<String, dynamic> r, {
    required double availableWidth,
  }) {
    final imageWidth = availableWidth < 340 ? availableWidth : 340.0;
    final hasFile =
        r['has_photo'] == true ||
        ((r['photo_file_id'] ?? '') as String).isNotEmpty;
    if (!hasFile) {
      return SizedBox(
        width: imageWidth,
        child: Card(
          color: Theme.of(context).cardColor,
          child: const SizedBox(
            height: 210,
            child: Center(child: Text('بلا صورة في هذا البلاغ')),
          ),
        ),
      );
    }
    return ConstrainedBox(
      constraints: BoxConstraints(maxWidth: imageWidth),
      child: Card(
        color: Theme.of(context).cardColor,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            FutureBuilder<Uint8List>(
              future: _photoFuture,
              builder: (context, snapshot) {
                if (snapshot.hasData) {
                  return Image.memory(
                    snapshot.data!,
                    fit: BoxFit.contain,
                    width: imageWidth,
                    errorBuilder: (context, error, stackTrace) =>
                        _photoError(context, 'استجابة الخادم ليست صورة صالحة.'),
                  );
                }
                if (snapshot.hasError) {
                  final error = snapshot.error;
                  final message = error is ApiException
                      ? _photoErrorMessage(error)
                      : 'تعذّر الاتصال بخدمة الصور.';
                  return _photoError(context, message);
                }
                return const SizedBox(
                  height: 210,
                  child: Center(child: CircularProgressIndicator()),
                );
              },
            ),
            Padding(
              padding: const EdgeInsets.all(8),
              child: OutlinedButton.icon(
                onPressed: () => _downloadPhoto(),
                icon: const Icon(Icons.download),
                label: const Text('تنزيل الصورة'),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _downloadPhoto() async {
    try {
      await widget.api.download('/api/admin/photo/${widget.rid}?download=true');
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    }
  }

  String _photoErrorMessage(ApiException error) => switch (error.code) {
    'no_photo' => 'لا يوجد ملف صورة محفوظ لهذا البلاغ.',
    'photo_unavailable' =>
      'تعذّر جلب الصورة من Telegram؛ قد يكون الملف غير متاح.',
    'unauthorized' => 'انتهت صلاحية جلسة الإدارة. سجّل الدخول من جديد.',
    'network_error' => 'تعذّر الاتصال بخدمة الصور.',
    _ => 'تعذّر تحميل الصورة (${error.code}).',
  };

  Widget _photoError(BuildContext context, String message) => SizedBox(
    height: 210,
    child: Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            Icons.broken_image_outlined,
            color: Theme.of(context).colorScheme.onSurfaceVariant,
          ),
          const SizedBox(height: 8),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Text(
              message,
              textAlign: TextAlign.center,
              style: TextStyle(
                color: Theme.of(context).colorScheme.onSurfaceVariant,
              ),
            ),
          ),
          TextButton.icon(
            onPressed: _retryPhoto,
            icon: const Icon(Icons.refresh),
            label: const Text('إعادة المحاولة'),
          ),
        ],
      ),
    ),
  );

  Widget _info(BuildContext context, Map<String, dynamic> r) {
    final status = (r['status'] ?? '-') as String;
    final st = kStatusStyles[status];
    final link = (r['post_link'] ?? '') as String;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            StateBadge(status, color: st?.$1 ?? Colors.grey),
            const SizedBox(width: 8),
            Text(
              fmt((r['created_at'] ?? '') as String),
              textDirection: TextDirection.ltr,
              style: TextStyle(
                fontSize: 12,
                color: Theme.of(context).colorScheme.onSurfaceVariant,
              ),
            ),
          ],
        ),
        const SizedBox(height: 10),
        Row(
          children: [
            UserActivityIndicator(lastSeen: r['user_last_at'] as String?),
            const SizedBox(width: 8),
            Expanded(child: _kv('المتبلّغ', (r['full_name'] ?? '—') as String)),
          ],
        ),
        _kv('معرّف المستخدم', '${r['user_id'] ?? '—'}'),
        _kv('حساب تلغرام', '@${r['username'] ?? '—'}'),
        _kv('الاسناد الذاتي', (r['suggested_type'] ?? '—') as String),
        _kv('التصنيف الحالي', (r['violation_type'] ?? '—') as String),
        _kv('ملاحظة إدارية', (r['admin_note'] ?? '—') as String),
        _kv('الأولوية', priorityLabel('${r['priority'] ?? 'normal'}')),
        _kv('المشرف المكلّف', '${r['assigned_to'] ?? 'غير مسند'}'),
        if ((r['due_at'] ?? '').toString().isNotEmpty)
          _kv('موعد المتابعة', fmt(r['due_at'] as String)),
        if ((r['followup_response'] ?? '').toString().isNotEmpty)
          _kv('رد المتبلّغ الإضافي', r['followup_response'] as String),
        if (link.isNotEmpty) ...[
          const Text('رابط المخالفة:'),
          SelectableText(
            link,
            textDirection: TextDirection.ltr,
            style: const TextStyle(color: Color(0xFF8AB4FF), fontSize: 13),
          ),
        ],
        const SizedBox(height: 8),
        const Text('وصف البلاغ:'),
        SelectableText((r['description'] ?? '—') as String),
      ],
    );
  }

  Widget _kv(String k, String v) => Padding(
    padding: const EdgeInsets.only(bottom: 4),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(width: 110, child: Text(k)),
        Expanded(child: SelectableText(v.isEmpty ? '—' : v)),
      ],
    ),
  );

  Widget _actions(BuildContext context, Map<String, dynamic> r, String status) {
    return SectionCard(
      'الإجراءات — تحكم كامل بالبلاغ',
      icon: Icons.bolt,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text('سير العمل', style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: 8),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              SizedBox(
                width: 180,
                child: DropdownButtonFormField<String>(
                  initialValue: _priority,
                  decoration: const InputDecoration(
                    labelText: 'الأولوية',
                    isDense: true,
                  ),
                  items: const [
                    DropdownMenuItem(value: 'low', child: Text('منخفضة')),
                    DropdownMenuItem(value: 'normal', child: Text('عادية')),
                    DropdownMenuItem(value: 'high', child: Text('عالية')),
                    DropdownMenuItem(value: 'urgent', child: Text('عاجلة')),
                  ],
                  onChanged: (value) {
                    if (value != null) setState(() => _priority = value);
                  },
                ),
              ),
              SizedBox(
                width: 220,
                child: DropdownButtonFormField<int?>(
                  initialValue: _assignedTo,
                  decoration: const InputDecoration(
                    labelText: 'المشرف المكلّف',
                    isDense: true,
                  ),
                  items: [
                    const DropdownMenuItem<int?>(
                      value: null,
                      child: Text('غير مسند'),
                    ),
                    if (_assignedTo != null && !_adminIds.contains(_assignedTo))
                      DropdownMenuItem<int?>(
                        value: _assignedTo,
                        child: Text('مشرف سابق ${_assignedTo!}'),
                      ),
                    for (final id in _adminIds)
                      DropdownMenuItem<int?>(
                        value: id,
                        child: Text('مشرف $id'),
                      ),
                  ],
                  onChanged: (value) => setState(() => _assignedTo = value),
                ),
              ),
              OutlinedButton.icon(
                onPressed: _chooseDueAt,
                icon: const Icon(Icons.event_outlined),
                label: Text(
                  _dueAt == null
                      ? 'تحديد موعد'
                      : fmt(_dueAt!.toIso8601String()),
                ),
              ),
              if (_dueAt != null)
                IconButton(
                  tooltip: 'مسح الموعد',
                  onPressed: () => setState(() => _dueAt = null),
                  icon: const Icon(Icons.event_busy_outlined),
                ),
              FilledButton.tonalIcon(
                onPressed: _saveWorkflow,
                icon: const Icon(Icons.save_outlined),
                label: const Text('حفظ سير العمل'),
              ),
            ],
          ),
          const Divider(height: 24),
          TextField(
            controller: _followup,
            maxLines: 2,
            maxLength: 1000,
            decoration: const InputDecoration(
              labelText: 'طلب معلومات إضافية من المتبلّغ',
              hintText:
                  'يرسل الطلب عبر Telegram، ويستطيع المبلّغ الرد بـ /reply',
              alignLabelWithHint: true,
            ),
          ),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: OutlinedButton.icon(
              onPressed: _requestFollowup,
              icon: const Icon(Icons.help_outline),
              label: const Text('إرسال طلب معلومات'),
            ),
          ),
          const Divider(height: 22),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final s in kStatusOrder)
                FilledButton.tonal(
                  style: FilledButton.styleFrom(
                    backgroundColor: status == s
                        ? kStatusStyles[s]?.$1.withOpacity(0.25)
                        : null,
                  ),
                  onPressed: () => _changeStatus(context, s),
                  child: Text(s),
                ),
            ],
          ),
          const Divider(height: 22),
          LayoutBuilder(
            builder: (context, constraints) {
              final width = constraints.maxWidth < 500
                  ? constraints.maxWidth
                  : 320.0;
              return Wrap(
                spacing: 8,
                runSpacing: 8,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  SizedBox(
                    width: width,
                    child: DropdownButton<String?>(
                      value: _typeSel,
                      isExpanded: true,
                      hint: const Text('(بلا تصنيف)'),
                      items: [
                        const DropdownMenuItem<String?>(
                          value: '',
                          child: Text('(بلا تصنيف)'),
                        ),
                        for (final t in widget.types)
                          DropdownMenuItem<String?>(value: t, child: Text(t)),
                      ],
                      onChanged: (v) => setState(() => _typeSel = v),
                    ),
                  ),
                  OutlinedButton(
                    onPressed: () => _saveType(context),
                    child: const Text('حفظ التصنيف'),
                  ),
                ],
              );
            },
          ),
          const SizedBox(height: 10),
          TextField(
            controller: _note,
            decoration: InputDecoration(
              labelText: 'ملاحظة إدارية داخلية',
              hintText: 'تظهر في التقارير فقط — لا تصل للمتبلّغ',
              suffixIcon: IconButton(
                tooltip: 'حفظ',
                onPressed: () => _saveNote(context),
                icon: const Icon(Icons.save_outlined),
              ),
            ),
          ),
          const Divider(height: 22),
          TextField(
            controller: _reply,
            maxLines: 3,
            maxLength: 2000,
            decoration: const InputDecoration(
              labelText: 'رد الجهة المختصة (يصل للمتبلّغ برسالة رسمية)',
              alignLabelWithHint: true,
            ),
          ),
          if (_templates.isNotEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 8),
              child: Wrap(
                spacing: 6,
                runSpacing: 6,
                children: [
                  for (final t in _templates)
                    ActionChip(
                      label: Text(
                        t.length > 34 ? '${t.substring(0, 34)}…' : t,
                        style: const TextStyle(fontSize: 12),
                      ),
                      tooltip: t,
                      onPressed: () => setState(() => _reply.text = t),
                    ),
                ],
              ),
            ),
          Wrap(
            spacing: 10,
            runSpacing: 8,
            alignment: WrapAlignment.spaceBetween,
            children: [
              FilledButton.icon(
                onPressed: () => _sendReply(context),
                icon: const Icon(Icons.send),
                label: const Text('أرسل الرد'),
              ),
              OutlinedButton.icon(
                style: OutlinedButton.styleFrom(
                  foregroundColor: Colors.red.shade300,
                ),
                onPressed: () => _delete(context),
                icon: const Icon(Icons.delete_forever, size: 18),
                label: const Text('حذف نهائي'),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Future<void> _chooseDueAt() async {
    final now = DateTime.now();
    final date = await showDatePicker(
      context: context,
      initialDate: _dueAt ?? now,
      firstDate: DateTime(now.year - 1),
      lastDate: DateTime(now.year + 10),
    );
    if (date == null || !mounted) return;
    final time = await showTimePicker(
      context: context,
      initialTime: TimeOfDay.fromDateTime(_dueAt ?? now),
    );
    if (time == null || !mounted) return;
    setState(() {
      _dueAt = DateTime(
        date.year,
        date.month,
        date.day,
        time.hour,
        time.minute,
      );
    });
  }

  Future<void> _saveWorkflow() async {
    try {
      await widget.api.put('/api/admin/reports/${widget.rid}/workflow', {
        'priority': _priority,
        'assigned_to': _assignedTo,
        'due_at': _dueAt?.toIso8601String(),
      });
      if (!mounted) return;
      toast(context, 'حُفظ سير العمل ✅');
      widget.onChanged();
      _fetch();
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    }
  }

  Future<void> _requestFollowup() async {
    final note = _followup.text.trim();
    if (note.length < 3) {
      toast(context, 'اكتب المعلومات المطلوبة من المتبلّغ', error: true);
      return;
    }
    try {
      await widget.api.post('/api/admin/reports/${widget.rid}/followup', {
        'note': note,
      });
      if (!mounted) return;
      setState(() => _followup.clear());
      toast(context, 'أُرسل طلب المعلومات عبر Telegram ✅');
      _fetch();
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    }
  }

  // -------- الإجراءات --------
  Future<void> _changeStatus(BuildContext context, String newStatus) async {
    final r = _r!;
    if (newStatus == r['status']) {
      toast(context, 'الحالة نفسها بالفعل');
      return;
    }
    final Map<String, dynamic> body;
    if (newStatus == 'مرفوض') {
      // سبب الرفض إلزامي — يصل للمتبلّغ برسالة
      final reason = await requiredTextDialog(
        context,
        title: 'سبب الرفض — إلزامي',
        hint: 'اكتب سبب الرفض (سيصل للمتبلّغ برسالة)…',
      );
      if (reason == null) return;
      body = {'status': 'rejected', 'reason': reason};
    } else {
      final okd = await confirmDialog(
        context,
        'تغيير الحالة',
        'تحويل الحالة إلى «$newStatus»؟',
      );
      if (!okd) return;
      final map = <String, String>{
        'جديد': 'new',
        'قيد المراجعة': 'review',
        'معتمد': 'approved',
      };
      body = {'status': map[newStatus]!};
    }
    try {
      final d = await widget.api.post(
        '/api/admin/reports/${widget.rid}/status',
        body,
      );
      if (!mounted) return;
      toast(
        context,
        d['notified'] == true
            ? 'تحُدث الحالة ووُصل للمتبلّغ ✅'
            : 'تحُدث الحالة (لم يصل الإشعار — ربما حجب البوت)',
      );
      _bust = DateTime.now().millisecondsSinceEpoch;
      widget.onChanged();
      _fetch();
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    }
  }

  Future<void> _saveType(BuildContext context) async {
    try {
      await widget.api.post('/api/admin/reports/${widget.rid}/type', {
        'violation_type': _typeSel ?? '',
      });
      if (mounted) toast(context, 'حُفظ التصنيف ✅');
      widget.onChanged();
      _fetch();
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    }
  }

  Future<void> _saveNote(BuildContext context) async {
    try {
      await widget.api.post('/api/admin/reports/${widget.rid}/note', {
        'note': _note.text.trim(),
      });
      if (mounted) toast(context, 'حُفظت الملاحظة ✅');
      _fetch();
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    }
  }

  Future<void> _sendReply(BuildContext context) async {
    final t = _reply.text.trim();
    if (t.length < 2) {
      toast(context, 'الرد قصير جداً — اكتب نصاً واضحاً', error: true);
      return;
    }
    try {
      await widget.api.post('/api/admin/reports/${widget.rid}/reply', {
        'text': t,
      });
      if (!mounted) return;
      setState(() => _reply.clear());
      toast(context, 'أُرسل الرد للمتبلّغ ✅');
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    }
  }

  Future<void> _delete(BuildContext context) async {
    final seq = (_r?['seq'] ?? '') as String;
    if (seq.isEmpty) return;
    final typed = await requiredTextDialog(
      context,
      title: 'حذف نهائي!',
      hint: 'اكتب «$seq» للتأكيد (حذف لا رجعة فيه)',
      mustMatch: seq,
      danger: true,
      maxLines: 1,
    );
    if (typed == null) return;
    try {
      await widget.api.del('/api/admin/reports/${widget.rid}', {
        'confirm_seq': seq,
      });
      if (!mounted) return;
      toast(context, 'حُذف البلاغ نهائياً');
      widget.onChanged();
      Navigator.of(context).pop();
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    }
  }
}
