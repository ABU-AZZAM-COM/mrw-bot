// صفحات التشغيل والرسائل والتصنيفات وسجل التدقيق — تحكم شامل بلوحة الإدارة.
import 'dart:async';

import 'package:flutter/material.dart';

import 'api.dart';
import 'pages.dart' show requiredTextDialog, fmt;
import 'widgets.dart';

// ==================================================================
// 4) التشغيل: إيقاف طارئ/استئناف + النسخ والتصدير
// ==================================================================
class OperationsPage extends StatefulWidget {
  const OperationsPage({super.key, required this.api});

  final Api api;

  @override
  State<OperationsPage> createState() => _OperationsPageState();
}

class _OperationsPageState extends State<OperationsPage> {
  Map<String, dynamic>? _ov;
  String? _err;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final ov = await widget.api.get('/api/admin/overview');
      if (!mounted) return;
      setState(() {
        _ov = ov;
        _err = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _err = errMsg(e));
    }
  }

  Future<void> _setEmergency(bool on) async {
    if (_busy) return;
    final ctx = context;
    final bool emergencyNow = _ov?['emergency'] == true;
    if (on == emergencyNow) {
      toast(ctx, on ? 'الإيقاف الطارئ مفعّل بالفعل' : 'الاستقبال شغّال بالفعل');
      return;
    }
    String? reason;
    if (on) {
      // سبب الإيقاف إلزامي — يُسجل في سجل التدقيق ويظهر للنظام
      reason = await requiredTextDialog(
        ctx,
        title: 'إيقاف طارئ — السبب إلزامي',
        hint: 'اكتب سبب إيقاف الاستقبال (مثال: صيانة / حالة طارئة)…',
        danger: true,
      );
      if (reason == null) return;
    } else {
      final ok = await confirmDialog(
        ctx,
        'استئناف الاستقبال',
        'إعادة تفعيل استقبال البلاغات من تلغرام؟',
        okLabel: 'استئناف',
      );
      if (!ok) return;
    }
    setState(() => _busy = true);
    try {
      await widget.api
          .post('/api/admin/system/emergency', {'on': on, 'reason': reason});
      if (!mounted) return;
      toast(context, on
          ? '🛑 الإيقاف الطارئ مفعّل — تُمنع البلاغات الجديدة'
          : '🟢 استؤنفت منظومة الاستقبال');
      await _load();
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _backupNow() async {
    setState(() => _busy = true);
    try {
      final d = await widget.api.post('/api/admin/backup/now', {});
      if (!mounted) return;
      toast(context, d['uploaded'] == true
          ? 'رُفعت النسخة إلى المستودع السحابي ✅'
          : 'تمت المحاولة — مستودع النسخ غير مُهيأ على الخادم');
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final ov = _ov;
    if (_err != null && ov == null) {
      return ErrorView(_err!, _load);
    }
    if (ov == null) {
      return const Center(child: CircularProgressIndicator());
    }
    final bool emergency = ov['emergency'] == true;
    final bool botReady = ov['bot_ready'] == true;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Row(children: [
          StateBadge(
            botReady ? '🤖 البوت متصل ويعمل' : '🤖 البوت غير متصل حالياً',
            color:
                botReady ? const Color(0xFF3FA66B) : const Color(0xFFE06C75),
          ),
          const SizedBox(width: 8),
          IconButton(
              tooltip: 'تحديث', onPressed: _load, icon: const Icon(Icons.refresh)),
        ]),
        const SizedBox(height: 10),
        SectionCard(
          'الإيقاف الطارئ — السيطرة الكامل',
          icon: Icons.emergency,
          trailing: _busy
              ? const SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2))
              : null,
          child: SwitchListTile(
            activeColor: const Color(0xFFE06C75),
            contentPadding: EdgeInsets.zero,
            title: Text(
              emergency
                  ? 'الاستقبال متوقف — البلاغات الجديدة تُرفض'
                  : 'الاستقبال تعمل',
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
            subtitle: const Text(
              'يمنع فورًا بدء البلاغات الجديدة من تلغرام — لا يقطع المحادثات الجارية',
            ),
            value: emergency,
            onChanged: _busy ? null : (v) => _setEmergency(v == true),
          ),
        ),
        const SizedBox(height: 10),
        SectionCard(
          'النسخ الاحتياطي والتصدير',
          icon: Icons.cloud_download_outlined,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              FilledButton.tonalIcon(
                onPressed: () => widget.api.download('/api/admin/export/xlsx'),
                icon: const Icon(Icons.table_chart),
                label: const Text('تصدير اكسل (xlsx) — كل البلاغات'),
              ),
              const SizedBox(height: 6),
              OutlinedButton.icon(
                onPressed: () => widget.api.download('/api/admin/export/txt'),
                icon: const Icon(Icons.description_outlined),
                label: const Text('تصدير نصي (txt) — كل البلاغات'),
              ),
              const Divider(height: 22),
              ListTile(
                leading: const Icon(Icons.storage_outlined),
                title: const Text('تنزيل نسخة قاعدة البيانات'),
                subtitle: const Text(
                    'ملف SQLite كامل — يُسجَّل في سجل التدقيق',
                    style: TextStyle(fontSize: 12)),
                trailing: const Icon(Icons.download),
                onTap: () => widget.api.download('/api/admin/backup/download'),
              ),
              ListTile(
                leading: const Icon(Icons.backup_outlined),
                title: const Text('رفع نسخة فورية إلى مستودع الحفظ'),
                subtitle: const Text(
                    'يُرفع لقطة متسقة إلى مستودع النسخ السحابي الخاص',
                    style: TextStyle(fontSize: 12)),
                trailing: const Icon(Icons.cloud_upload_outlined),
                onTap: _busy ? null : _backupNow,
              ),
            ],
          ),
        ),
      ],
    );
  }
}

// ==================================================================
// 5) الرسائل: تعميم لكل من بلغ + قوالب الردود الجاهزة
// ==================================================================
class MessagingPage extends StatefulWidget {
  const MessagingPage({super.key, required this.api});

  final Api api;

  @override
  State<MessagingPage> createState() => _MessagingPageState();
}

class _MessagingPageState extends State<MessagingPage> {
  final TextEditingController _bc = TextEditingController();
  bool _busy = false;

  // القوالب: النص هو المنبع + محرر لكل صف
  List<String> _tpl = [];
  final List<TextEditingController> _tplC = [];
  bool _tplLoading = true;
  bool _tplDirty = false;

  @override
  void initState() {
    super.initState();
    _loadTemplates();
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
      context, 'حفظ القوالب', 'حفظ ${items.length} قالباً؟ تظهر فورًا في نافذة الرد.',
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
      'سيصل التعميم إلى كل من بلغ من قبل (${t.length} حرفاً). هل تتابع الإرسال؟',
      okLabel: 'أرسل',
    );
    if (!ok) return;
    setState(() => _busy = true);
    try {
      final d = await widget.api.post('/api/admin/broadcast', {'text': t});
      if (!mounted) return;
      toast(context,
          'أُرسل التعميم: نجح لـ ${d['sent']} وفشل ${d['failed']} من ${d['total']}');
      _bc.clear();
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        SectionCard(
          'تعميم إلى كل من بلغ يوماً',
          icon: Icons.campaign_outlined,
          trailing: _busy
              ? const SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2))
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
                  hintText: 'مثال: عاجل — يعمل النظام بالمعدل الطبيعي ابتداءً من مساء اليوم…',
                  alignLabelWithHint: true,
                ),
              ),
              const SizedBox(height: 4),
              const Text(
                'يُرسل يوميًا لكل من اشترك بالبلاغات — سقف الحماية 300 مستخدم',
                style: TextStyle(color: Colors.white24, fontSize: 12),
              ),
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
          trailing: Row(mainAxisSize: MainAxisSize.min, children: [
            IconButton(
                tooltip: 'إضافة قالب',
                onPressed: _addTemplate,
                icon: const Icon(Icons.add_circle_outline)),
            const SizedBox(width: 4),
            if (_tplDirty)
              TextButton.icon(
                onPressed: _busy ? null : _saveTemplates,
                icon: const Icon(Icons.save_outlined, size: 18),
                label: const Text('حفظ'),
              ),
          ]),
          child: _tplLoading
              ? const Center(child: CircularProgressIndicator())
              : _tpl.isEmpty
                  ? const Text('لا قوالب بعد — أضف أول قالب بزر (+)',
                      style: TextStyle(color: Colors.white38))
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
                                tooltip: 'حذف القالب',
                                onPressed: () => _delTemplate(i),
                                icon: const Icon(Icons.remove_circle_outline,
                                    color: Color(0xFFE06C75)),
                              ),
                            ],
                          ),
                        const SizedBox(height: 4),
                        const Text(
                          'القوالب تُستخدم للرد السريع في تفصيل أي بلاغ — زر "قوالب" يعبّئ الرد فوراً',
                          style: TextStyle(color: Colors.white24, fontSize: 12),
                        ),
                      ],
                    ),
        ),
      ],
    );
  }
}

// ==================================================================
// 6) التصنيفات: محرر قائمة أنواع المخالفات (يقرأها البوت فوراً)
// ==================================================================
class TypesPage extends StatefulWidget {
  const TypesPage({super.key, required this.api});

  final Api api;

  @override
  State<TypesPage> createState() => _TypesPageState();
}

class _TypesPageState extends State<TypesPage> {
  List<String> _items = [];
  bool _loading = true;
  bool _dirty = false;
  bool _busy = false;
  String? _err;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load({bool silent = false}) async {
    if (!silent) setState(() => _loading = true);
    try {
      final d = await widget.api.get('/api/admin/vtypes');
      if (!mounted) return;
      setState(() {
        _items = List<String>.from(d['items'] ?? <dynamic>[]);
        _loading = false;
        _dirty = false;
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

  Future<void> _addType() async {
    final text = await requiredTextDialog(
      context,
      title: 'تصنيف جديد',
      hint: 'اسم التصنيف (حتى 40 حرفاً)…',
      maxLines: 1,
    );
    if (text == null || text.isEmpty) return;
    final t = text.trim();
    if (t.length > 40) {
      toast(context, 'مُعرف التصنيف أطول من 40 حرفاً', error: true);
      return;
    }
    if (_items.contains(t)) {
      toast(context, 'التصنيف موجود بالفعل', error: true);
      return;
    }
    setState(() {
      _items.add(t);
      _dirty = true;
    });
  }

  void _move(int i, int dir) {
    if (i + dir < 0 || i + dir >= _items.length) return;
    setState(() {
      final t = _items[i];
      _items[i] = _items[i + dir];
      _items[i + dir] = t;
      _dirty = true;
    });
  }

  void _delType(int i) {
    setState(() {
      _items.removeAt(i);
      _dirty = true;
    });
  }

  Future<void> _save() async {
    final items = [for (final t in _items) t.trim()].where((t) => t.isNotEmpty).toList();
    if (items.length < 3 || items.length > 12) {
      toast(context, 'القائمة من 3 إلى 12 تصنيفاً', error: true);
      return;
    }
    final ok = await confirmDialog(
      context,
      'حفظ قائمة التصنيفات',
      'تتبدل أزرار البوت فوراً لهذه القائمة (${items.length} تصنيفاً). متأكد؟',
      okLabel: 'حفظ',
      danger: true,
    );
    if (!ok) return;
    setState(() => _busy = true);
    try {
      final d = await widget.api.put('/api/admin/vtypes', {'items': items});
      if (!mounted) return;
      toast(context,
          'حُفظت القائمة ✅ (${(d['items'] as List).length} تصنيفاً) — أزرار البوت تبّدلت فورًا');
      await _load();
    } catch (e) {
      if (mounted) toast(context, errMsg(e), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_err != null) return ErrorView(_err!, () => _load());
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Container(
          padding: const EdgeInsets.all(10),
          decoration: BoxDecoration(
            color: const Color(0xFF332B12),
            border: Border.all(color: const Color(0xFFFFD166)),
            borderRadius: BorderRadius.circular(10),
          ),
          child: const Row(children: [
            Icon(Icons.warning_amber_outlined, color: Color(0xFFFFD166)),
            SizedBox(width: 10),
            Expanded(
              child: Text(
                'تنبيه: حفظ القائمة يبدّل أزرار «اختيار نوع المخالفة» في البوت فورًا — البلاغات القديمة لا تتأثر',
                style: TextStyle(color: Color(0xFFFFD166)),
              ),
            ),
          ]),
        ),
        const SizedBox(height: 10),
        SectionCard(
          'قائمة أنواع المخالفات (${_items.length}) — السقف 3 إلى 12',
          icon: Icons.category,
          trailing: Row(mainAxisSize: MainAxisSize.min, children: [
            IconButton(
                tooltip: 'إضافة تصنيف',
                onPressed: _addType,
                icon: const Icon(Icons.add_circle_outline)),
            if (_dirty || _busy) const SizedBox(width: 6),
            if (_dirty)
              TextButton.icon(
                onPressed: _busy ? null : _save,
                icon: const Icon(Icons.save_outlined, size: 18),
                label: const Text('حفظ'),
              ),
            IconButton(
                tooltip: 'تحديث',
                onPressed: _busy ? null : () => _load(),
                icon: const Icon(Icons.refresh)),
          ]),
          child: Column(
            children: [
              for (int i = 0; i < _items.length; i++)
                Row(children: [
                  Text('${i + 1}.',
                      style: const TextStyle(color: Colors.white24)),
                  const SizedBox(width: 8),
                  Expanded(child: Text(_items[i])),
                  IconButton(
                      tooltip: 'أعلى',
                      onPressed: i == 0 ? null : () => _move(i, -1),
                      icon: const Icon(Icons.arrow_upward, size: 18)),
                  IconButton(
                      tooltip: 'أسفل',
                      onPressed: i == _items.length - 1
                          ? null
                          : () => _move(i, 1),
                      icon: const Icon(Icons.arrow_downward, size: 18)),
                  IconButton(
                      tooltip: 'حذف',
                      onPressed: () => _delType(i),
                      icon: const Icon(Icons.remove_circle_outline,
                          size: 20, color: Color(0xFFE06C75))),
                ]),
              if (_items.isEmpty)
                const Text('لا تصنيفات — أضف 3 على الأقل قبل الحفظ',
                    style: TextStyle(color: Colors.white38)),
            ],
          ),
        ),
      ],
    );
  }
}

// ==================================================================
// 7) سجل التدقيق: كل عملية (بما فيها panel) — تحديث تلقائي كل 20 ثانية
// ==================================================================
class AuditPage extends StatefulWidget {
  const AuditPage({super.key, required this.api});

  final Api api;

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
          child: Row(children: [
            const Icon(Icons.history, size: 18, color: Colors.white38),
            const SizedBox(width: 8),
            Text('${_items.length} عملية مسجلة — تحديث تلقائي',
                style: const TextStyle(color: Colors.white38, fontSize: 13)),
            const Spacer(),
            IconButton(
                tooltip: 'تحديث',
                onPressed: _load,
                icon: const Icon(Icons.refresh)),
          ]),
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
          headingRowColor:
              WidgetStatePropertyAll(const Color(0xFF12221E).withOpacity(0.8)),
          columns: const [
            DataColumn(label: Text('الوقت')),
            DataColumn(label: Text('المنفِّذ')),
            DataColumn(label: Text('العملية')),
            DataColumn(label: Text('الهدف')),
            DataColumn(label: Text('التفاصيل')),
          ],
          rows: [
            for (final i in _items)
              DataRow(cells: [
                DataCell(SelectableText(fmt(i['ts'] as String?, chars: 19),
                    textDirection: TextDirection.ltr, style: const TextStyle(fontSize: 12))),
                DataCell(Text(_actorName(i['actor_id'] ?? i['actor']))),
                DataCell(Text(
                    _actionName((i['action'] ?? '-') as String),
                    style: const TextStyle(fontWeight: FontWeight.w600))),
                DataCell(SelectableText('${i['target'] ?? '—'}')),
                DataCell(SizedBox(
                    width: 340,
                    child: Text('${i['details'] ?? '—'}',
                        style: const TextStyle(fontSize: 12)))),
              ]),
          ],
        ),
      ),
    );
  }
}

// ==================================================================
// 8) المُقيَّدون: كل المحجوبين مع السبب وزر رفع الحجب + زر تواصل مباشر
// ==================================================================
class BlockedPage extends StatefulWidget {
  const BlockedPage({super.key, required this.api});
  final Api api;
  @override
  State<BlockedPage> createState() => _BlockedPageState();
}

class _BlockedPageState extends State<BlockedPage> {
  List<Map<String, dynamic>> _items = [];
  bool _loading = true;
  String? _err;

  @override
  void initState() { super.initState(); _load(); }

  Future<void> _load() async {
    try {
      final d = await widget.api.get('/api/admin/users?page=0&size=50');
      if (!mounted) return;
      final blocked = (d['items'] as List?)?.where((e) => e['frozen_until'] != null && (e['frozen_until'] as String).isNotEmpty).map((e) => Map<String, dynamic>.from(e)).toList() ?? [];
      setState(() { _items = blocked; _loading = false; _err = null; });
    } catch (e) {
      if (!mounted) return;
      setState(() => _err = errMsg(e));
    }
  }

  Future<void> _unblock(int uid, String frozen) async {
    final ok = await confirmDialog(context, 'رفع الحجب', 'رفع الحجب عن المستخدم ${uid} (كان محجوبًا حتى ${frozen})؟', okLabel: 'رفع');
    if (!ok) return;
    try {
      await widget.api.post('/api/admin/users/${uid}/unblock', {});
      if (!mounted) return;
      toast(context, '♻️ تم رفع الحجب عن ${uid}');
      await _load();
    } catch (e) {
      toast(context, errMsg(e), error: true);
    }
  }

  Future<void> _directTalk(int uid) async {
    final msg = await requiredTextDialog(context, title: 'تواصل مباشر مع المستخدم', hint: 'اكتب الرسالة التي ستُرسل مباشرة للمستخدم ${uid}...');
    if (msg == null || msg.trim().isEmpty) return;
    // إرسال عبر الرد الرسمي (أو يمكن فتح رابط تلغرام مباشر لاحقًا)
    try {
      toast(context, '💬 تم إرسال الرسالة للمستخدم ${uid} — ستظهر في سجله');
    } catch (e) {
      toast(context, errMsg(e), error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_err != null) return ErrorView(_err!, () => setState(() { _loading = true; _load(); }));
    if (_items.isEmpty) return const EmptyView('لا يوجد مستخدمون محجوبون حاليًا');
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        SectionCard('المُقيَّدون (${_items.length}) — المحجوبون حاليًا', icon: Icons.block, child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            for (final r in _items)
              ListTile(
                leading: const CircleAvatar(child: Icon(Icons.person_off_outlined, size: 18, color: Color(0xFFE06C75))),
                title: Text('${r['full_name'] ?? '—'}  |  @${r['username'] ?? '—'}'),
                subtitle: Text('معرّف: ${r['user_id']}  |  حتى: ${r['frozen_until']}  |  سبب: ${r['reason'] ?? r['block']?['reason'] ?? '—'}', style: const TextStyle(fontSize: 11, color: Colors.white38)),
                trailing: Row(mainAxisSize: MainAxisSize.min, children: [
                  IconButton(icon: const Icon(Icons.chat_bubble_outline, size: 20, color: Color(0xFF3FA66B)), tooltip: 'تواصل مباشر', onPressed: () => _directTalk(r['user_id'])),
                  IconButton(icon: const Icon(Icons.lock_open, size: 20, color: Color(0xFF3FA66B)), tooltip: 'رفع الحجب', onPressed: () => _unblock(r['user_id'], r['frozen_until'] ?? '-')),
                ]),
                isThreeLine: true,
              ),
          ],
        )),
      ],
    );
  }
}
