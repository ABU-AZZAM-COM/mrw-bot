import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';
import 'overview_page.dart' show fmt;

class UsersPage extends StatefulWidget {
  const UsersPage({super.key, required this.api});

  final AdminApi api;

  @override
  State<UsersPage> createState() => _UsersPageState();
}

class _UsersPageState extends State<UsersPage> {
  static const int _pageSize = 25;

  List<Map<String, dynamic>> _items = [];
  final TextEditingController _q = TextEditingController();
  int _page = 0;
  int _total = 0;
  bool _loading = true;
  String? _err;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _q.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _err = null;
    });
    final q = <String, String>{
      'page': '$_page',
      'size': '$_pageSize',
      if (_q.text.trim().isNotEmpty) 'q': _q.text.trim(),
    };
    try {
      final data = await widget.api.get('/api/admin/users', q);
      if (!mounted) return;
      setState(() {
        _items = (data['items'] ?? <dynamic>[])
            .map((e) => (e as Map).cast<String, dynamic>())
            .toList();
        _total = data['total'] as int? ?? 0;
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
                    hintText: 'بحث: الاسم / حساب تلغرام / رقم المعرف',
                  ),
                  onSubmitted: (_) {
                    setState(() => _page = 0);
                    _load();
                  },
                ),
              ),
              const SizedBox(width: 8),
              IconButton.filledTonal(
                tooltip: 'بحث',
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
          padding: const EdgeInsets.symmetric(vertical: 6),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              IconButton(
                tooltip: 'الصفحة التالية',
                onPressed: (_page + 1) * _pageSize >= _total
                    ? null
                    : () {
                        setState(() => _page++);
                        _load();
                      },
                icon: const Icon(Icons.chevron_right),
              ),
              Text(
                'الصفحة ${_page + 1} — الإجمالي $_total',
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

  Widget _body(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_err != null) return ErrorView(_err!, _load);
    if (_items.isEmpty) return const EmptyView('لا متببلّغين بعد');
    return SingleChildScrollView(
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: DataTable(
          showCheckboxColumn: false,
          headingRowColor: WidgetStatePropertyAll(
            Theme.of(context).colorScheme.surfaceContainerHighest,
          ),
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
      ),
    );
  }

  DataRow _row(BuildContext context, Map<String, dynamic> u) {
    final frozen = (u['frozen_until'] as String?) ?? '';
    final freezeDate = DateTime.tryParse(frozen);
    final blocked =
        frozen.startsWith('9999-') ||
        (freezeDate != null && freezeDate.isAfter(DateTime.now().toUtc()));
    return DataRow(
      cells: [
        DataCell(
          SelectableText(
            '${u['user_id'] ?? '-'}',
            textDirection: TextDirection.ltr,
          ),
        ),
        DataCell(Text('${u['full_name'] ?? '-'}')),
        DataCell(Text('@${u['username'] ?? '—'}')),
        DataCell(Text('${u['reports_count'] ?? 0}')),
        DataCell(
          blocked
              ? const StateBadge('محجوب', color: Color(0xFFE06C75))
              : const StateBadge('نشط'),
        ),
        DataCell(
          Text(
            fmt((u['last_at'] ?? '') as String),
            textDirection: TextDirection.ltr,
            style: const TextStyle(fontSize: 12),
          ),
        ),
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
  const _UserCard({
    required this.api,
    required this.uid,
    required this.onChanged,
  });

  final AdminApi api;
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
          decoration: BoxDecoration(
            color: Theme.of(context).colorScheme.surfaceContainerHighest,
            borderRadius: BorderRadius.vertical(top: Radius.circular(12)),
          ),
          child: Row(
            children: [
              Expanded(
                child: Text(
                  'بطاقة المتبلّغ — ${p['full_name'] ?? '-'}',
                  style: const TextStyle(
                    fontWeight: FontWeight.w700,
                    fontSize: 15,
                  ),
                ),
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
                const Text(
                  'بلاغات المتبلّغ',
                  style: TextStyle(fontWeight: FontWeight.w700),
                ),
                const SizedBox(height: 6),
                if (_reports.isEmpty)
                  const Text('لا بلاغات مسجلة')
                else
                  for (final r in _reports.take(40))
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 2),
                      child: Row(
                        children: [
                          SelectableText(
                            '${r['seq'] ?? '-'}',
                            textDirection: TextDirection.ltr,
                            style: const TextStyle(fontWeight: FontWeight.w600),
                          ),
                          const SizedBox(width: 10),
                          Text('${r['status'] ?? '-'}'),
                          const Spacer(),
                          Text('${r['violation_type'] ?? '—'}'),
                          const SizedBox(width: 10),
                          Text(
                            fmt((r['created_at'] ?? '') as String),
                            textDirection: TextDirection.ltr,
                            style: const TextStyle(fontSize: 11),
                          ),
                        ],
                      ),
                    ),
                const Divider(height: 22),
                Text(
                  'الحجب',
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
                const SizedBox(height: 6),
                Row(
                  children: [
                    Radio<bool>(
                      value: true,
                      groupValue: _perm,
                      onChanged: (v) => setState(() => _perm = v ?? false),
                    ),
                    const Text('دائم'),
                    const SizedBox(width: 12),
                    Radio<bool>(
                      value: false,
                      groupValue: _perm,
                      onChanged: (v) => setState(() => _perm = v ?? true),
                    ),
                    const Text('مؤقت:'),
                    SizedBox(
                      width: 90,
                      child: TextField(
                        enabled: !_perm,
                        keyboardType: TextInputType.number,
                        controller: TextEditingController(text: '$_hours'),
                        onChanged: (t) => _hours = int.tryParse(t) ?? 72,
                        decoration: const InputDecoration(
                          isDense: true,
                          suffixText: 'ساعة',
                        ),
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: TextField(
                        controller: _reason,
                        decoration: const InputDecoration(
                          hintText: 'سبب الحجب (يُوصى به)',
                          isDense: true,
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 10),
                Row(
                  children: [
                    FilledButton.icon(
                      style: FilledButton.styleFrom(
                        backgroundColor: Colors.red.shade700,
                      ),
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
          child: Text(
            k,
            style: TextStyle(
              color: Theme.of(context).colorScheme.onSurfaceVariant,
            ),
          ),
        ),
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
      context,
      'تأكيد الحجب',
      'منع «$name» ${_perm ? 'بشكل دائم' : 'لمدة $_hours ساعة'}؟',
      danger: true,
    );
    if (!ok) return;
    try {
      await widget.api.post('/api/admin/users/${widget.uid}/block', {
        'duration': _perm ? 'permanent' : '$_hours',
        'reason': _reason.text.trim().isEmpty
            ? 'بدون سبب موثق'
            : _reason.text.trim(),
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
    final ok = await confirmDialog(
      context,
      'رفع الحجب',
      'رفع الحجب عن المتببلّغ؟',
    );
    if (!ok) return;
    try {
      final result = await widget.api.post(
        '/api/admin/users/${widget.uid}/unblock',
        {},
      );
      if (!mounted) return;
      toast(
        context,
        result['notified'] == true
            ? 'رُفع الحجب وأُشعر المستخدم ✅'
            : 'رُفع الحجب لكن تعذّر إشعار المستخدم',
        error: result['notified'] != true,
      );
      widget.onChanged();
      _fetch();
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    }
  }
}
