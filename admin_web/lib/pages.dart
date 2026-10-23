// الصفحات الثلاث الأساسية: النظرة العامة، البلاغات، المتبلّغون.
// كل شيء عربي RTL وخفيف (لا مكتبات خارجية سوى http).
import 'dart:async';

import 'package:flutter/material.dart';

import 'api.dart';
import 'widgets.dart';

// الحالات مخزنة بالعربية في القاعدة — نعرضها كما هي مع لون ورمز.
const Map<String, (Color, IconData)> kStatusStyles = {
  'جديد': (Color(0xFFFFD166), Icons.fiber_new_outlined),
  'قيد المراجعة': (Color(0xFF8AB4FF), Icons.search),
  'معتمد': (Color(0xFF3FA66B), Icons.verified_outlined),
  'مرفوض': (Color(0xFFE06C75), Icons.cancel_outlined),
};
const List<String> kStatusOrder = ['جديد', 'قيد المراجعة', 'معتمد', 'مرفوض'];

String fmt(String? iso, {int chars = 16}) {
  if (iso == null) return '-';
  return iso.length > chars ? iso.substring(0, chars) : iso;
}

// ==================================================================
// 1) النظرة العامة
// ==================================================================
class OverviewPage extends StatefulWidget {
  const OverviewPage({super.key, required this.api});

  final Api api;

  @override
  State<OverviewPage> createState() => _OverviewPageState();
}

class _OverviewPageState extends State<OverviewPage> {
  Map<String, dynamic>? _ov;
  Map<String, dynamic>? _st;
  String? _err;

  @override
  void initState() {
    super.initState();
    _load();
    // تحديث لحظي كل 7 ثوانٍ — يتوقف تلقائياً عند نزع الصفحة.
    Timer.periodic(const Duration(seconds: 7), (t) {
      if (!mounted) {
        t.cancel();
        return;
      }
      _load(silent: true);
    });
  }

  Future<void> _load({bool silent = false}) async {
    if (!silent) setState(() => _err = null);
    try {
      final ov = await widget.api.get('/api/admin/overview');
      Map<String, dynamic>? st;
      try {
        st = await widget.api.get('/api/admin/stats');
      } catch (_) {
        st = null; // الإحصاءات ليست حاكمة للنظرة العامة
      }
      if (!mounted) return;
      setState(() {
        _ov = ov;
        _st = st;
        _err = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _err = silent ? (_err ?? errMsg(e)) : errMsg(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final ov = _ov;
    if (_err != null && ov == null) {
      return ErrorView(_err!, () => _load());
    }
    if (ov == null) {
      return const Center(child: CircularProgressIndicator());
    }
    final counts = (ov['counts'] ?? <String, dynamic>{}) as Map<String, dynamic>;
    final st = _st;
    final bool emergency = ov['emergency'] == true;
    return RefreshIndicator(
      onRefresh: () => _load(),
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Row(
            children: [
              StateBadge(
                ov['bot_ready'] == true ? '🤖 البوت جاهز' : '🤖 البوت غير جاهز!',
                color: ov['bot_ready'] == true
                    ? const Color(0xFF3FA66B)
                    : const Color(0xFFE06C75),
              ),
              const SizedBox(width: 8),
              StateBadge(
                emergency ? '🛑 إيقاف طارئ مفعّل' : '🟢 الاستقبال شغّال',
                color: emergency
                    ? const Color(0xFFE06C75)
                    : const Color(0xFF3FA66B),
              ),
              const SizedBox(width: 8),
              Text('تحديث: ${TimeOfDay.now().format(context)}',
                  style: const TextStyle(color: Colors.white38, fontSize: 12)),
            ],
          ),
          const SizedBox(height: 12),
          GridView.count(
            crossAxisCount: 4,
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            childAspectRatio: 2.6,
            mainAxisSpacing: 8,
            crossAxisSpacing: 8,
            children: [
              StatCard(
                  title: 'جديد',
                  value: '${counts['جديد'] ?? 0}',
                  color: const Color(0xFFFFD166),
                  icon: Icons.fiber_new_outlined),
              StatCard(
                  title: 'قيد المراجعة',
                  value: '${counts['قيد المراجعة'] ?? 0}',
                  color: const Color(0xFF8AB4FF),
                  icon: Icons.search),
              StatCard(
                  title: 'معتمد',
                  value: '${counts['معتمد'] ?? 0}',
                  color: const Color(0xFF3FA66B),
                  icon: Icons.verified_outlined),
              StatCard(
                  title: 'مرفوض',
                  value: '${counts['مرفوض'] ?? 0}',
                  color: const Color(0xFFE06C75),
                  icon: Icons.cancel_outlined),
              StatCard(
                  title: 'الإجمالي',
                  value: '${counts['total'] ?? 0}',
                  color: const Color(0xFF9AB8AA),
                  icon: Icons.inventory_2_outlined),
              StatCard(
                  title: 'آخر 24 ساعة',
                  value: '${counts['last24'] ?? 0}',
                  color: const Color(0xFFC4A2FF),
                  icon: Icons.schedule),
              StatCard(
                  title: 'متوسط الأسبوع/يوم',
                  value: '${_st?['week_avg'] ?? '-'}',
                  color: const Color(0xFF8BD4AE),
                  icon: Icons.insights),
              StatCard(
                  title: 'حالة الاستقبال',
                  value: emergency ? 'متوقف' : 'يعمل',
                  color: emergency
                      ? const Color(0xFFE06C75)
                      : const Color(0xFF3FA66B),
                  icon: Icons.emergency_outlined),
            ],
          ),
          const SizedBox(height: 10),
          if (st != null) ...[
            SectionCard(
              'حركة البلاغات — آخر 7 أيام',
              icon: Icons.stacked_bar_chart,
              child: WeekChart(
                  items: (st['daily7'] ?? <dynamic>[]).cast<Map<String, dynamic>>()),
            ),
            const SizedBox(height: 10),
            SectionCard(
              'توزيع أنواع المخالفات',
              icon: Icons.category,
              child: ((st['by_type'] ?? []) as List).isEmpty
                  ? const Text('لا توجد بيانات بعد')
                  : Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: [
                        for (final row in st['by_type'])
                          Chip(label: Text('${row['vtype']} (${row['n']})')),
                      ],
                    ),
            ),
            const SizedBox(height: 10),
            SectionCard(
              'أكثر المتبلّغين نشاطاً',
              icon: Icons.leaderboard_outlined,
              child: ((st['top_users'] ?? []) as List).isEmpty
                  ? const Text('لا توجد بيانات بعد')
                  : Column(
                      children: [
                        for (final row in st['top_users'])
                          ListTile(
                            dense: true,
                            contentPadding: EdgeInsets.zero,
                            leading: const Icon(Icons.person_outline),
                            title: Text('${row['full_name'] ?? '-'}'),
                            subtitle: SelectableText('ID ${row['user_id']}',
                                textDirection: TextDirection.ltr),
                            trailing: Text('${row['n']} بلاغ',
                                style:
                                    const TextStyle(fontWeight: FontWeight.w700)),
                          ),
                      ],
                    ),
            ),
          ],
        ],
      ),
    );
  }
}

/// رسم أعمدة بسيط مبني يدوياً — بلا مكتبات خارجية.
class WeekChart extends StatelessWidget {
  const WeekChart({super.key, required this.items});

  final List<Map<String, dynamic>> items;

  @override
  Widget build(BuildContext context) {
    final now = DateTime.now();
    final days = List<String>.generate(7, (i) {
      final d = now.subtract(Duration(days: 6 - i));
      return '${d.year.toString().padLeft(4, '0')}-'
          '${d.month.toString().padLeft(2, '0')}-'
          '${d.day.toString().padLeft(2, '0')}';
    });
    final byDay = <String, int>{
      for (final e in items) (e['day'] ?? '') as String: (e['n'] ?? 0) as int,
    };
    int maxN = 1;
    for (final d in days) {
      maxN = (byDay[d] ?? 0) > maxN ? byDay[d] ?? 0 : maxN;
    }
    const wdNames = ['إثن', 'ثلا', 'أرب', 'خمي', 'جمع', 'سبت', 'أحد'];
    return SizedBox(
      height: 160,
      child: Row(
        children: [
          for (int i = 0; i < days.length; i++)
            Expanded(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  Text('${byDay[days[i]] ?? 0}',
                      style: TextStyle(
                          fontSize: 12,
                          color: (byDay[days[i]] ?? 0) > 0
                              ? Colors.white70
                              : Colors.white24)),
                  const SizedBox(height: 4),
                  Flexible(
                    child: Align(
                      alignment: Alignment.bottomCenter,
                      child: Container(
                        width: 26,
                        height: ((byDay[days[i]] ?? 0) / maxN) * 92 + 8,
                        decoration: BoxDecoration(
                          color: const Color(0xFF3FA66B)
                              .withOpacity((byDay[days[i]] ?? 0) > 0 ? 1 : 0.22),
                          borderRadius: BorderRadius.circular(6),
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(wdNames[now.subtract(Duration(days: 6 - i)).weekday - 1],
                      style:
                          const TextStyle(fontSize: 11, color: Colors.white38)),
                  Text(days[i].substring(5),
                      style:
                          const TextStyle(fontSize: 10, color: Colors.white24)),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

// ==================================================================
// 2) البلاغات
// ==================================================================
class ReportsPage extends StatefulWidget {
  const ReportsPage({super.key, required this.api});

  final Api api;

  @override
  State<ReportsPage> createState() => _ReportsPageState();
}

class _ReportsPageState extends State<ReportsPage> {
  List<Map<String, dynamic>> _items = [];
  List<String> _types = [];
  int _total = 0;
  int _page = 0;
  static const int _size = 25;
  String? _status;
  String? _type;
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
            () => _types = List<String>.from(meta['vtypes'] ?? <dynamic>[]));
      } catch (_) {
        // قائمة التصنيفات ليست حاكمة
      }
    }
    _load();
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

  void _shiftPage(int d) {
    setState(() => _page = (_page + d).clamp(0, 100000));
    _load();
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: Wrap(
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
                      value: null, child: Text('كل الحالات')),
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
              DropdownButton<String?>(
                value: _type,
                hint: const Text('كل التصنيفات'),
                underline: const SizedBox(),
                items: [
                  const DropdownMenuItem<String?>(
                      value: null, child: Text('كل التصنيفات')),
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
                width: 230,
                child: TextField(
                  controller: _q,
                  decoration: const InputDecoration(
                      prefixIcon: Icon(Icons.search, size: 20),
                      hintText: 'بحث: رقم/اسم/وصف/رابط/معرف',
                      isDense: true),
                  onSubmitted: (_) {
                    setState(() => _page = 0);
                    _load();
                  },
                ),
              ),
              IconButton.filledTonal(
                  tooltip: 'بحث/تحديث',
                  onPressed: () {
                    setState(() => _page = 0);
                    _load();
                  },
                  icon: const Icon(Icons.search)),
              IconButton(
                  tooltip: 'تحديث', onPressed: _load, icon: const Icon(Icons.refresh)),
            ],
          ),
        ),
        const Divider(height: 1),
        Expanded(child: _body(context)),
        Padding(
          padding: const EdgeInsets.all(10),
          child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
            IconButton(
                onPressed: _page == 0 ? null : () => _shiftPage(-1),
                icon: const Icon(Icons.chevron_right)),
            Text('الصفحة ${_page + 1} — الإجمالي $_total',
                style: const TextStyle(color: Colors.white54)),
            IconButton(
                onPressed: (_page + 1) * _size >= _total ? null : () => _shiftPage(1),
                icon: const Icon(Icons.chevron_left)),
          ]),
        ),
      ],
    );
  }

  Widget _body(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_err != null) return ErrorView(_err!, _load);
    if (_items.isEmpty) return const EmptyView('لا بلاغات مطابقة');
    return SingleChildScrollView(
      child: DataTable(
        showCheckboxColumn: false,
        headingRowColor:
            WidgetStatePropertyAll(const Color(0xFF12221E).withOpacity(0.8)),
        columns: const [
          DataColumn(label: Text('الرقم')),
          DataColumn(label: Text('الحالة')),
          DataColumn(label: Text('الاسم')),
          DataColumn(label: Text('المعرف')),
          DataColumn(label: Text('التصنيف')),
          DataColumn(label: Text('التاريخ')),
        ],
        rows: [for (final r in _items) _row(context, r)],
      ),
    );
  }

  DataRow _row(BuildContext context, Map<String, dynamic> r) {
    final String status = (r['status'] ?? '-') as String;
    final style = kStatusStyles[status];
    return DataRow(
      color: WidgetStatePropertyAll(status == 'جديد'
          ? const Color(0xFF16261E)
          : Colors.transparent),
      cells: [
        DataCell(SelectableText('${r['seq'] ?? '-'}',
            textDirection: TextDirection.ltr,
            style: const TextStyle(fontWeight: FontWeight.w600))),
        DataCell(Row(children: [
          Icon(style?.$2 ?? Icons.help_outline,
              size: 15, color: style?.$1 ?? Colors.grey),
          const SizedBox(width: 6),
          Text(status, style: TextStyle(color: style?.$1 ?? Colors.grey)),
        ])),
        DataCell(Text('${r['full_name'] ?? '-'}')),
        DataCell(SelectableText('${r['user_id'] ?? '-'}',
            textDirection: TextDirection.ltr)),
        DataCell(Text('${r['violation_type'] ?? '—'}')),
        DataCell(Text(fmt((r['created_at'] ?? '') as String),
            textDirection: TextDirection.ltr,
            style: const TextStyle(fontSize: 12))),
      ],
      onSelectChanged: (_) => _detailDialog(context, r),
    );
  }

  Future<void> _detailDialog(BuildContext context, Map<String, dynamic> r) async {
    final rid = (r['id'] ?? 0) as int;
    await showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (ctx) => Dialog(
        child: SizedBox(
          width: 940,
          height: 680,
          child: _ReportDetail(
              api: widget.api, rid: rid, types: _types, onChanged: _load),
        ),
      ),
    );
  }
}

// ==================================================================
// تفصيل البلاغ + كامل الإجراءات
// ==================================================================
class _ReportDetail extends StatefulWidget {
  const _ReportDetail(
      {super.key,
      required this.api,
      required this.rid,
      required this.types,
      required this.onChanged});

  final Api api;
  final int rid;
  final List<String> types;
  final VoidCallback onChanged;

  @override
  State<_ReportDetail> createState() => _ReportDetailState();
}

class _ReportDetailState extends State<_ReportDetail> {
  Map<String, dynamic>? _r;
  List<String> _templates = [];
  int _bust = DateTime.now().millisecondsSinceEpoch;
  final TextEditingController _reply = TextEditingController();
  final TextEditingController _note = TextEditingController();
  String? _typeSel;

  @override
  void initState() {
    super.initState();
    _fetch();
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
    if (!mounted) return;
    setState(() {
      _r = item;
      _templates = tpl;
      _typeSel = (item['violation_type'] ?? '') as String?;
      _note.text = (item['admin_note'] ?? '') as String? ?? '';
    });
  }

  @override
  Widget build(BuildContext context) {
    final r = _r;
    if (r == null || r.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }
    final status = (r['status'] ?? '-') as String;
    return Column(
      children: [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
          decoration: const BoxDecoration(
              color: Color(0xFF12221E),
              borderRadius: BorderRadius.vertical(top: Radius.circular(12))),
          child: Row(children: [
            Text('تفصيل البلاغ ${r['seq'] ?? '-'}',
                style:
                    const TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
            const Spacer(),
            IconButton(
                tooltip: 'تحديث',
                onPressed: () {
                  _bust = DateTime.now().millisecondsSinceEpoch;
                  _fetch();
                },
                icon: const Icon(Icons.refresh, size: 20)),
            IconButton(
                tooltip: 'إغلاق',
                onPressed: () => Navigator.of(context).pop(),
                icon: const Icon(Icons.close, size: 20)),
          ]),
        ),
        Expanded(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(14),
            child: Column(
              children: [
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    _photo(context, r),
                    const SizedBox(width: 14),
                    Expanded(child: _info(context, r)),
                  ],
                ),
                const SizedBox(height: 12),
                _actions(context, r, status),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _photo(BuildContext context, Map<String, dynamic> r) {
    final hasFile = ((r['photo_file_id'] ?? '') as String).isNotEmpty;
    if (!hasFile) {
      return SizedBox(
        width: 330,
        child: Card(
          color: const Color(0xFF0D1B16),
          child: const SizedBox(
            height: 210,
            child: Center(child: Text('بلا صورة في هذا البلاغ')),
          ),
        ),
      );
    }
    return ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 350),
      child: Card(
        color: const Color(0xFF0D1B16),
        child: Image.network(
          widget.api.photoUrl(widget.rid, bust: '$_bust'),
          fit: BoxFit.contain,
          width: 340,
          errorBuilder: (_, __, ___) => const SizedBox(
            height: 210,
            child: Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(Icons.broken_image_outlined, color: Colors.white24),
                  SizedBox(height: 8),
                  Text('الصورة غير متاحة مؤقتاً',
                      style: TextStyle(color: Colors.white38)),
                ],
              ),
            ),
          ),
          loadingBuilder: (c, w, p) {
            final ev = p?.expectedTotalBytes;
            return SizedBox(
              height: 210,
              child: Center(
                child: p == null || ev == null
                    ? const CircularProgressIndicator()
                    : CircularProgressIndicator(
                        value: p.cumulativeBytesLoaded / ev),
              ),
            );
          },
        ),
      ),
    );
  }

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
            Text(fmt((r['created_at'] ?? '') as String),
                textDirection: TextDirection.ltr,
                style: const TextStyle(fontSize: 12, color: Colors.white38)),
          ],
        ),
        const SizedBox(height: 10),
        _kv('المتبلّغ', (r['full_name'] ?? '—') as String),
        _kv('معرّف المستخدم', '${r['user_id'] ?? '—'}'),
        _kv('حساب تلغرام', '@${r['username'] ?? '—'}'),
        _kv('الاسناد الذاتي', (r['suggested_type'] ?? '—') as String),
        _kv('التصنيف الحالي', (r['violation_type'] ?? '—') as String),
        _kv('ملاحظة إدارية', (r['admin_note'] ?? '—') as String),
        if (link.isNotEmpty) ...[
          const Text('رابط المخالفة:',
              style: TextStyle(color: Colors.white24)),
          SelectableText(link,
              textDirection: TextDirection.ltr,
              style: const TextStyle(color: Color(0xFF8AB4FF), fontSize: 13)),
        ],
        const SizedBox(height: 8),
        const Text('وصف البلاغ:', style: TextStyle(color: Colors.white24)),
        SelectableText((r['description'] ?? '—') as String),
      ],
    );
  }

  Widget _kv(String k, String v) => Padding(
        padding: const EdgeInsets.only(bottom: 4),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(
                width: 110,
                child: Text(k, style: const TextStyle(color: Colors.white24))),
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
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final s in kStatusOrder)
                FilledButton.tonal(
                  style: FilledButton.styleFrom(
                    backgroundColor: status == s
                        ? kStatusStyles[s]?.$1?.withOpacity(0.25)
                        : null,
                  ),
                  onPressed: () => _changeStatus(context, s),
                  child: Text(s),
                ),
            ],
          ),
          const Divider(height: 22),
          Row(
            children: [
              Expanded(
                child: DropdownButton<String?>(
                  value: _typeSel,
                  isExpanded: true,
                  hint: const Text('(بلا تصنيف)'),
                  items: [
                    const DropdownMenuItem<String?>(
                        value: '', child: Text('(بلا تصنيف)')),
                    for (final t in widget.types)
                      DropdownMenuItem<String?>(value: t, child: Text(t)),
                  ],
                  onChanged: (v) => setState(() => _typeSel = v),
                ),
              ),
              const SizedBox(width: 8),
              OutlinedButton(
                  onPressed: () => _saveType(context),
                  child: const Text('حفظ التصنيف')),
            ],
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
                    icon: const Icon(Icons.save_outlined))),
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
                      label: Text(t.length > 34 ? '${t.substring(0, 34)}…' : t,
                          style: const TextStyle(fontSize: 12)),
                      tooltip: t,
                      onPressed: () => setState(() => _reply.text = t),
                    ),
                ],
              ),
            ),
          Row(
            children: [
              FilledButton.icon(
                onPressed: () => _sendReply(context),
                icon: const Icon(Icons.send),
                label: const Text('أرسل الرد'),
              ),
              const Spacer(),
              OutlinedButton.icon(
                style: OutlinedButton.styleFrom(
                    foregroundColor: Colors.red.shade300),
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
          context, 'تغيير الحالة', 'تحويل الحالة إلى «$newStatus»؟');
      if (!okd) return;
      final map = <String, String>{
        'جديد': 'new',
        'قيد المراجعة': 'review',
        'معتمد': 'approved',
      };
      body = {'status': map[newStatus]!};
    }
    try {
      final d =
          await widget.api.post('/api/admin/reports/${widget.rid}/status', body);
      if (!mounted) return;
      toast(context, d['notified'] == true
          ? 'تحُدث الحالة ووُصل للمتبلّغ ✅'
          : 'تحُدث الحالة (لم يصل الإشعار — ربما حجب البوت)');
      _bust = DateTime.now().millisecondsSinceEpoch;
      widget.onChanged();
      _fetch();
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    }
  }

  Future<void> _saveType(BuildContext context) async {
    try {
      await widget.api.post('/api/admin/reports/${widget.rid}/type',
          {'violation_type': _typeSel ?? ''});
      if (mounted) toast(context, 'حُفظ التصنيف ✅');
      widget.onChanged();
      _fetch();
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    }
  }

  Future<void> _saveNote(BuildContext context) async {
    try {
      await widget.api.post('/api/admin/reports/${widget.rid}/note',
          {'note': _note.text.trim()});
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
      await widget.api.post('/api/admin/reports/${widget.rid}/reply', {'text': t});
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
      await widget.api.del('/api/admin/reports/${widget.rid}', {'confirm_seq': seq});
      if (!mounted) return;
      toast(context, 'حُذف البلاغ نهائياً');
      widget.onChanged();
      Navigator.of(context).pop();
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    }
  }
}

// حوار نصّ إلزامي (سبب الرفض / تأكيد الحذف) — يعيد النص أو null عند إلغاء.
Future<String?> requiredTextDialog(
  BuildContext context, {
  required String title,
  required String hint,
  String? mustMatch,
  bool danger = false,
  int maxLines = 3,
}) async {
  final c = TextEditingController();
  final res = await showDialog<String>(
    context: context,
    barrierDismissible: false,
    builder: (ctx) => AlertDialog(
      title: Text(title),
      content: TextField(
        controller: c,
        autofocus: true,
        maxLines: maxLines,
        maxLength: mustMatch != null ? 120 : 600,
        decoration: InputDecoration(hintText: hint),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.of(ctx).pop(null),
            child: const Text('إلغاء')),
        FilledButton(
          style: danger
              ? FilledButton.styleFrom(backgroundColor: Colors.red.shade700)
              : null,
          onPressed: () {
            final t = c.text.trim();
            if (t.isEmpty) return;
            if (mustMatch != null && t != mustMatch) return;
            Navigator.of(ctx).pop(t);
          },
          child: const Text('تأكيد'),
        ),
      ],
    ),
  );
  return res;
}

// ==================================================================
// 3) المتبلّغون
// ==================================================================
class UsersPage extends StatefulWidget {
  const UsersPage({super.key, required this.api});

  final Api api;

  @override
  State<UsersPage> createState() => _UsersPageState();
}

class _UsersPageState extends State<UsersPage> {
  List<Map<String, dynamic>> _items = [];
  final TextEditingController _q = TextEditingController();
  bool _loading = true;
  String? _err;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _err = null;
    });
    final q = <String, String>{
      'size': '100',
      if (_q.text.trim().isNotEmpty) 'q': _q.text.trim(),
    };
    try {
      final data = await widget.api.get('/api/admin/users', q);
      if (!mounted) return;
      setState(() {
        _items = (data['items'] ?? <dynamic>[])
            .map((e) => (e as Map).cast<String, dynamic>())
            .toList();
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

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _q,
                  decoration: const InputDecoration(
                      hintText: 'بحث: الاسم / حساب تلغرام / رقم المعرف'),
                  onSubmitted: (_) => _load(),
                ),
              ),
              const SizedBox(width: 8),
              IconButton.filledTonal(
                  tooltip: 'بحث', onPressed: _load, icon: const Icon(Icons.search)),
              IconButton(
                  tooltip: 'تحديث', onPressed: _load, icon: const Icon(Icons.refresh)),
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
    if (_err != null) return ErrorView(_err!, _load);
    if (_items.isEmpty) return const EmptyView('لا متببلّغين بعد');
    return SingleChildScrollView(
      child: DataTable(
        showCheckboxColumn: false,
        headingRowColor:
            WidgetStatePropertyAll(const Color(0xFF12221E).withOpacity(0.8)),
        columns: const [
          DataColumn(label: Text('المعرف')),
          DataColumn(label: Text('الاسم')),
          DataColumn(label: Text('حساب تلغرام')),
          DataColumn(label: Text('البلاغات')),
          DataColumn(label: Text('الحالة')),
          DataColumn(label: Text('آخر ظهور')),
        ],
        rows: [for (final u in _items) _row(context, u)],
      ),
    );
  }

  DataRow _row(BuildContext context, Map<String, dynamic> u) {
    final frozen = ((u['frozen_until'] ?? '') ?? '') as String;
    return DataRow(
      cells: [
        DataCell(SelectableText('${u['user_id'] ?? '-'}',
            textDirection: TextDirection.ltr)),
        DataCell(Text('${u['full_name'] ?? '-'}')),
        DataCell(Text('@${u['username'] ?? '—'}')),
        DataCell(Text('${u['reports_count'] ?? 0}')),
        DataCell(((u['frozen_until'] ?? '') ?? '').toString().isEmpty
            ? const StateBadge('نشط')
            : const StateBadge('محجوب', color: Color(0xFFE06C75))),
        DataCell(Text(fmt((u['last_at'] ?? '') as String),
            textDirection: TextDirection.ltr,
            style: const TextStyle(fontSize: 12))),
      ],
      onSelectChanged: (_) =>
          _cardDialog(context, int.tryParse('${u['user_id']}') ?? 0),
    );
  }

  Future<void> _cardDialog(BuildContext context, int uid) async {
    await showDialog<void>(
      context: context,
      builder: (ctx) => Dialog(
        child: SizedBox(
          width: 780,
          height: 640,
          child: _UserCard(api: widget.api, uid: uid, onChanged: _load),
        ),
      ),
    );
  }
}

// ==================================================================
// بطاقة المتبلّغ + الحجب
// ==================================================================
class _UserCard extends StatefulWidget {
  const _UserCard(
      {super.key, required this.api, required this.uid, required this.onChanged});

  final Api api;
  final int uid;
  final VoidCallback onChanged;

  @override
  State<_UserCard> createState() => _UserCardState();
}

class _UserCardState extends State<_UserCard> {
  Map<String, dynamic>? _p;
  List<Map<String, dynamic>> _reports = [];
  bool _perm = false;
  int _hours = 72;
  final TextEditingController _reason = TextEditingController();

  @override
  void initState() {
    super.initState();
    _fetch();
  }

  Future<void> _fetch() async {
    try {
      final d = await widget.api.get('/api/admin/users/${widget.uid}');
      if (!mounted) return;
      setState(() {
        _p = (d['profile'] ?? <String, dynamic>{}) as Map<String, dynamic>;
        _reports = (d['reports'] ?? <dynamic>[])
            .map((e) => (e as Map).cast<String, dynamic>())
            .toList();
      });
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = _p;
    if (p == null) {
      return const Center(child: CircularProgressIndicator());
    }
    final b = (p['block'] ?? <String, dynamic>{}) as Map<String, dynamic>;
    final frozen = (b['frozen_until'] ?? '') as String;
    return Column(
      children: [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
          decoration: const BoxDecoration(
              color: Color(0xFF12221E),
              borderRadius: BorderRadius.vertical(top: Radius.circular(12))),
          child: Row(children: [
            Expanded(
              child: Text(
                  'بطاقة المتبلّغ — ${p['full_name'] ?? '-'}',
                  style:
                      const TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
            ),
            IconButton(
                tooltip: 'إغلاق',
                onPressed: () => Navigator.of(context).pop(),
                icon: const Icon(Icons.close, size: 20)),
          ]),
        ),
        Expanded(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(14),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                _kv('المعرف', '${p['user_id'] ?? '-'}'),
                _kv('الاسم', (p['full_name'] ?? '—') as String),
                _kv('حساب تلغرام', '@${p['username'] ?? '—'}'),
                _kv('إجمالي البلاغات', '${p['total_reports'] ?? 0}'),
                _kv('المعتمدة', '${p['approved_n'] ?? 0}'),
                _kv('المرفوضة', '${p['rejected_n'] ?? 0}'),
                _kv('أول ظهور', fmt(p['first_at'] as String?, chars: 19)),
                _kv('آخر ظهور', fmt(p['last_at'] as String?, chars: 19)),
                _kv(
                  'الحالة الإجمالية',
                  frozen.isEmpty
                      ? '🟢 نشط بلا حجب'
                      : '⛔ محجوب حتى ${fmt(frozen, chars: 19)}\nالسبب: ${b['reason'] ?? '—'}',
                ),
                const Divider(height: 22),
                const Text('بلاغات المتبلّغ',
                    style: TextStyle(fontWeight: FontWeight.w700)),
                const SizedBox(height: 6),
                if (_reports.isEmpty)
                  const Text('لا بلاغات مسجلة',
                      style: TextStyle(color: Colors.white38))
                else
                  for (final r in _reports.take(40))
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 2),
                      child: Row(
                        children: [
                          SelectableText('${r['seq'] ?? '-'}',
                              textDirection: TextDirection.ltr,
                              style: const TextStyle(fontWeight: FontWeight.w600)),
                          const SizedBox(width: 10),
                          Text('${r['status'] ?? '-'}'),
                          const Spacer(),
                          Text('${r['violation_type'] ?? '—'}'),
                          const SizedBox(width: 10),
                          Text(fmt((r['created_at'] ?? '') as String),
                              textDirection: TextDirection.ltr,
                              style: const TextStyle(
                                  fontSize: 11, color: Colors.white24)),
                        ],
                      ),
                    ),
                const Divider(height: 22),
                Text('الحجب',
                    style: const TextStyle(fontWeight: FontWeight.w700)),
                const SizedBox(height: 6),
                Row(
                  children: [
                    Radio<bool>(
                        value: true,
                        groupValue: _perm,
                        onChanged: (v) => setState(() => _perm = v ?? false)),
                    const Text('دائم'),
                    const SizedBox(width: 12),
                    Radio<bool>(
                        value: false,
                        groupValue: _perm,
                        onChanged: (v) => setState(() => _perm = v ?? true)),
                    const Text('مؤقت:'),
                    SizedBox(
                      width: 90,
                      child: TextField(
                        enabled: !_perm,
                        keyboardType: TextInputType.number,
                        controller: TextEditingController(text: '$_hours'),
                        onChanged: (t) => _hours = int.tryParse(t) ?? 72,
                        decoration:
                            const InputDecoration(isDense: true, suffixText: 'ساعة'),
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: TextField(
                        controller: _reason,
                        decoration: const InputDecoration(
                            hintText: 'سبب الحجب (يُوصى به)', isDense: true),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 10),
                Row(
                  children: [
                    FilledButton.icon(
                      style: FilledButton.styleFrom(
                          backgroundColor: Colors.red.shade700),
                      onPressed: () => _block(context),
                      icon: const Icon(Icons.block),
                      label: const Text('تنفيذ الحجب'),
                    ),
                    const SizedBox(width: 10),
                    OutlinedButton.icon(
                      onPressed: () => _unblock(context),
                      icon: const Icon(Icons.lock_open),
                      label: const Text('رفع الحجب'),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _kv(String k, String v) => Padding(
        padding: const EdgeInsets.only(bottom: 4),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(
                width: 120,
                child: Text(k, style: const TextStyle(color: Colors.white24))),
            Expanded(child: SelectableText(v.isEmpty ? '—' : v)),
          ],
        ),
      );

  Future<void> _block(BuildContext context) async {
    if (!_perm && _hours < 1) {
      toast(context, 'أدخل عدد ساعات صحيحاً', error: true);
      return;
    }
    final name = (_p?['full_name'] ?? '-') as String;
    final ok = await confirmDialog(
        context, 'تأكيد الحجب',
        'منع «$name» ${_perm ? 'بشكل دائم' : 'لمدة $_hours ساعة'}؟',
        danger: true);
    if (!ok) return;
    try {
      await widget.api.post('/api/admin/users/${widget.uid}/block', {
        'duration': _perm ? 'permanent' : '$_hours',
        'reason': _reason.text.trim().isEmpty ? 'بدون سبب موثق' : _reason.text.trim(),
      });
      if (!mounted) return;
      toast(context, 'حُجب المتبلّغ ✅');
      widget.onChanged();
      _fetch();
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    }
  }

  Future<void> _unblock(BuildContext context) async {
    final ok =
        await confirmDialog(context, 'رفع الحجب', 'رفع الحجب عن المتببلّغ؟');
    if (!ok) return;
    try {
      await widget.api.post('/api/admin/users/${widget.uid}/unblock', {});
      if (!mounted) return;
      toast(context, 'رُفع الحجب ✅');
      widget.onChanged();
      _fetch();
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    }
  }
}
