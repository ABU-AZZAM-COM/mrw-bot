import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';
import 'shared_dialogs.dart';

class TypesPage extends StatefulWidget {
  const TypesPage({super.key, required this.api});

  final AdminApi api;

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
    final items = [
      for (final t in _items) t.trim(),
    ].where((t) => t.isNotEmpty).toList();
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
      toast(
        context,
        'حُفظت القائمة ✅ (${(d['items'] as List).length} تصنيفاً) — أزرار البوت تبّدلت فورًا',
      );
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
          child: const Row(
            children: [
              Icon(Icons.warning_amber_outlined, color: Color(0xFFFFD166)),
              SizedBox(width: 10),
              Expanded(
                child: Text(
                  'تنبيه: حفظ القائمة يبدّل أزرار «اختيار نوع المخالفة» في البوت فورًا — البلاغات القديمة لا تتأثر',
                  style: TextStyle(color: Color(0xFFFFD166)),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 10),
        SectionCard(
          'قائمة أنواع المخالفات (${_items.length}) — السقف 3 إلى 12',
          icon: Icons.category,
          trailing: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              IconButton(
                tooltip: 'إضافة تصنيف',
                onPressed: _addType,
                icon: const Icon(Icons.add_circle_outline),
              ),
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
                icon: const Icon(Icons.refresh),
              ),
            ],
          ),
          child: Column(
            children: [
              for (int i = 0; i < _items.length; i++)
                Row(
                  children: [
                    Text(
                      '${i + 1}.',
                      style: const TextStyle(color: Colors.white24),
                    ),
                    const SizedBox(width: 8),
                    Expanded(child: Text(_items[i])),
                    IconButton(
                      tooltip: 'أعلى',
                      onPressed: i == 0 ? null : () => _move(i, -1),
                      icon: const Icon(Icons.arrow_upward, size: 18),
                    ),
                    IconButton(
                      tooltip: 'أسفل',
                      onPressed: i == _items.length - 1
                          ? null
                          : () => _move(i, 1),
                      icon: const Icon(Icons.arrow_downward, size: 18),
                    ),
                    IconButton(
                      tooltip: 'حذف',
                      onPressed: () => _delType(i),
                      icon: const Icon(
                        Icons.remove_circle_outline,
                        size: 20,
                        color: Color(0xFFE06C75),
                      ),
                    ),
                  ],
                ),
              if (_items.isEmpty)
                const Text(
                  'لا تصنيفات — أضف 3 على الأقل قبل الحفظ',
                  style: TextStyle(color: Colors.white38),
                ),
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
