import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';
import 'shared_dialogs.dart';

class MessagingPage extends StatefulWidget {
  const MessagingPage({super.key, required this.api});

  final AdminApi api;

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
      'سيصل التعميم إلى كل من بلغ من قبل (${t.length} حرفاً). هل تتابع الإرسال؟',
      okLabel: 'أرسل',
    );
    if (!ok) return;
    setState(() => _busy = true);
    try {
      final d = await widget.api.post('/api/admin/broadcast', {'text': t});
      if (!mounted) return;
      toast(
        context,
        'أُرسل التعميم: نجح لـ ${d['sent']} وفشل ${d['failed']} من ${d['total']}',
      );
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
              ? const Text(
                  'لا قوالب بعد — أضف أول قالب بزر (+)',
                  style: TextStyle(color: Colors.white38),
                )
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
