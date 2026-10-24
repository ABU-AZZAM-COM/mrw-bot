import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';
import 'overview_page.dart' show fmt, kStatusOrder, kStatusStyles;
import 'shared_dialogs.dart';

class ReportsPage extends StatefulWidget {
  const ReportsPage({super.key, required this.api});

  final AdminApi api;

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
          () => _types = List<String>.from(meta['vtypes'] ?? <dynamic>[]),
        );
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
                width: 230,
                child: TextField(
                  controller: _q,
                  decoration: const InputDecoration(
                    prefixIcon: Icon(Icons.search, size: 20),
                    hintText: 'بحث: رقم/اسم/وصف/رابط/معرف',
                    isDense: true,
                  ),
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
                icon: const Icon(Icons.search),
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
        Padding(
          padding: const EdgeInsets.all(10),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              IconButton(
                onPressed: _page == 0 ? null : () => _shiftPage(-1),
                icon: const Icon(Icons.chevron_right),
              ),
              Text(
                'الصفحة ${_page + 1} — الإجمالي $_total',
                style: const TextStyle(color: Colors.white54),
              ),
              IconButton(
                onPressed: (_page + 1) * _size >= _total
                    ? null
                    : () => _shiftPage(1),
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
    return SingleChildScrollView(
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: DataTable(
          showCheckboxColumn: false,
          headingRowColor: WidgetStatePropertyAll(
            const Color(0xFF12221E).withOpacity(0.8),
          ),
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
      ),
    );
  }

  DataRow _row(BuildContext context, Map<String, dynamic> r) {
    final String status = (r['status'] ?? '-') as String;
    final style = kStatusStyles[status];
    return DataRow(
      color: WidgetStatePropertyAll(
        status == 'جديد' ? const Color(0xFF16261E) : Colors.transparent,
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
        DataCell(
          Text(
            fmt((r['created_at'] ?? '') as String),
            textDirection: TextDirection.ltr,
            style: const TextStyle(fontSize: 12),
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
      builder: (ctx) => Dialog(
        child: SizedBox(
          width: 940,
          height: 680,
          child: _ReportDetail(
            api: widget.api,
            rid: rid,
            types: _types,
            onChanged: _load,
          ),
        ),
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
    required this.onChanged,
  });

  final AdminApi api;
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
            borderRadius: BorderRadius.vertical(top: Radius.circular(12)),
          ),
          child: Row(
            children: [
              Text(
                'تفصيل البلاغ ${r['seq'] ?? '-'}',
                style: const TextStyle(
                  fontWeight: FontWeight.w700,
                  fontSize: 15,
                ),
              ),
              const Spacer(),
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
                  Text(
                    'الصورة غير متاحة مؤقتاً',
                    style: TextStyle(color: Colors.white38),
                  ),
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
                        value: p.cumulativeBytesLoaded / ev,
                      ),
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
            Text(
              fmt((r['created_at'] ?? '') as String),
              textDirection: TextDirection.ltr,
              style: const TextStyle(fontSize: 12, color: Colors.white38),
            ),
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
          const Text('رابط المخالفة:', style: TextStyle(color: Colors.white24)),
          SelectableText(
            link,
            textDirection: TextDirection.ltr,
            style: const TextStyle(color: Color(0xFF8AB4FF), fontSize: 13),
          ),
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
          child: Text(k, style: const TextStyle(color: Colors.white24)),
        ),
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
                        ? kStatusStyles[s]?.$1.withOpacity(0.25)
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
                      value: '',
                      child: Text('(بلا تصنيف)'),
                    ),
                    for (final t in widget.types)
                      DropdownMenuItem<String?>(value: t, child: Text(t)),
                  ],
                  onChanged: (v) => setState(() => _typeSel = v),
                ),
              ),
              const SizedBox(width: 8),
              OutlinedButton(
                onPressed: () => _saveType(context),
                child: const Text('حفظ التصنيف'),
              ),
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
