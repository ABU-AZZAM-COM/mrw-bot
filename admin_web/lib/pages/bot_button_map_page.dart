import 'package:flutter/material.dart';

import '../api.dart';

/// إجراء متاح لزر قائمة المستخدم (يأتي من الخادم معرّفًا ومُتحقَّقًا منه).
class _MenuAction {
  const _MenuAction({required this.id, required this.label});

  final String id;
  final String label;
}

class _MenuButton {
  _MenuButton({
    required this.id,
    required this.label,
    required this.action,
  });

  final int id;
  String label;
  String action;

  Map<String, dynamic> toJson() => {'label': label, 'action': action};
}

/// محرر حي لأزرار قائمة المستخدم في البوت.
///
/// الحفظ هنا لا يعدّل نسخة محلية: يرسل التخطيط إلى الخادم الذي يخزّنه في
/// قاعدة البيانات ويطبّقه على البوت فورًا، فيراه المستخدمون في الرسالة
/// التالية دون إعادة نشر.
class BotButtonMapPage extends StatefulWidget {
  const BotButtonMapPage({super.key, required this.api});

  final AdminApi api;

  @override
  State<BotButtonMapPage> createState() => _BotButtonMapPageState();
}

class _BotButtonMapPageState extends State<BotButtonMapPage> {
  static const _defaultLimits = {
    'max_rows': 8,
    'max_row_buttons': 3,
    'max_buttons': 16,
    'max_label': 40,
  };

  final List<List<_MenuButton>> _rows = [];
  List<_MenuAction> _actions = const [];
  Map<String, int> _limits = Map<String, int>.from(_defaultLimits);
  bool _loading = true;
  bool _saving = false;
  bool _customized = false;
  bool _showPreview = false;
  String? _error;
  int _nextButtonId = 1;

  @override
  void initState() {
    super.initState();
    _load();
  }

  int get _maxRows => _limits['max_rows'] ?? 8;
  int get _maxRowButtons => _limits['max_row_buttons'] ?? 3;
  int get _maxButtons => _limits['max_buttons'] ?? 16;
  int get _maxLabel => _limits['max_label'] ?? 40;
  int get _totalButtons =>
      _rows.fold<int>(0, (sum, row) => sum + row.length);

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final data = await widget.api.get('/api/admin/bot-buttons');
      if (!mounted) return;
      setState(() {
        _applyServerPayload(data);
        _loading = false;
      });
    } on ApiException catch (exc) {
      if (!mounted) return;
      setState(() {
        _error = _describe(exc);
        _loading = false;
      });
    } catch (exc) {
      if (!mounted) return;
      setState(() {
        _error = 'تعذر تحميل أزرار البوت: $exc';
        _loading = false;
      });
    }
  }

  void _applyServerPayload(Map<String, dynamic> data) {
    _actions = [
      for (final item in (data['actions'] as List? ?? const []))
        if (item is Map)
          _MenuAction(
            id: '${item['id']}',
            label: '${item['label']}',
          ),
    ];
    final rawLimits = data['limits'];
    _limits = {
      for (final key in _defaultLimits.keys)
        key: rawLimits is Map && rawLimits[key] is num
            ? (rawLimits[key] as num).toInt()
            : _defaultLimits[key]!,
    };
    _customized = data['customized'] == true;
    _rows
      ..clear()
      ..addAll(_parseRows(data['items']));
  }

  List<List<_MenuButton>> _parseRows(Object? raw) {
    final rows = <List<_MenuButton>>[];
    if (raw is! List) return rows;
    for (final row in raw) {
      if (row is! List) continue;
      final buttons = <_MenuButton>[];
      for (final item in row) {
        if (item is! Map) continue;
        final label = '${item['label'] ?? ''}'.trim();
        final action = '${item['action'] ?? ''}'.trim();
        if (label.isEmpty || action.isEmpty) continue;
        buttons.add(
          _MenuButton(id: _nextButtonId++, label: label, action: action),
        );
      }
      if (buttons.isNotEmpty) rows.add(buttons);
    }
    return rows;
  }

  String _describe(ApiException exc) {
    switch (exc.code) {
      case 'unauthorized':
        return 'التوكن غير صالح أو انتهت الجلسة — أعد تسجيل الدخول.';
      case 'forbidden':
        return 'هذه العملية تتطلب صلاحية المالك.';
      case 'bad_bot_menu_layout':
        return 'التخطيط مرفوض: تحقق من طول النصوص وتكرار الأزرار والحدود.';
      case 'api_token_not_configured':
        return 'لم يُضبط ADMIN_API_TOKEN على الخادم.';
      default:
        return 'فشل الطلب (${exc.status}): ${exc.code}';
    }
  }

  _MenuAction? _actionById(String id) {
    for (final action in _actions) {
      if (action.id == id) return action;
    }
    return null;
  }

  _MenuAction? get _firstUnusedAction {
    final used = {
      for (final row in _rows)
        for (final button in row) button.action,
    };
    for (final action in _actions) {
      if (!used.contains(action.id)) return action;
    }
    return null;
  }

  String _labelFor(String actionId) =>
      _actionById(actionId)?.label ?? actionId;

  void _addRow() {
    if (_rows.length >= _maxRows) {
      _toast('وصلت للحد الأقصى من الصفوف ($_maxRows).');
      return;
    }
    final action = _firstUnusedAction;
    if (action == null) {
      _toast('كل الإجراءات المتاحة مستخدمة بالفعل. احذف زرًا أولًا.');
      return;
    }
    setState(() {
      _rows.add([
        _MenuButton(id: _nextButtonId++, label: action.label, action: action.id),
      ]);
    });
  }

  void _addButton(int rowIndex) {
    if (_totalButtons >= _maxButtons) {
      _toast('وصلت للحد الأقصى من الأزرار ($_maxButtons).');
      return;
    }
    if (_rows[rowIndex].length >= _maxRowButtons) {
      _toast('لا يمكن وضع أكثر من $_maxRowButtons أزرار في صف واحد.');
      return;
    }
    final action = _firstUnusedAction;
    if (action == null) {
      _toast('كل الإجراءات المتاحة مستخدمة بالفعل.');
      return;
    }
    setState(() {
      _rows[rowIndex].add(
        _MenuButton(id: _nextButtonId++, label: action.label, action: action.id),
      );
    });
  }

  void _removeButton(int rowIndex, int buttonIndex) {
    if (_totalButtons <= 1) {
      _toast('يجب أن تبقى زرًا واحدًا على الأقل.');
      return;
    }
    setState(() {
      _rows[rowIndex].removeAt(buttonIndex);
      if (_rows[rowIndex].isEmpty) _rows.removeAt(rowIndex);
    });
  }

  void _removeRow(int rowIndex) {
    if (_rows.length <= 1) {
      _toast('يجب أن يبقى صف واحد على الأقل.');
      return;
    }
    setState(() => _rows.removeAt(rowIndex));
  }

  void _moveRow(int rowIndex, int delta) {
    final target = rowIndex + delta;
    if (target < 0 || target >= _rows.length) return;
    setState(() {
      final row = _rows.removeAt(rowIndex);
      _rows.insert(target, row);
    });
  }

  void _moveButton(int rowIndex, int buttonIndex, int delta) {
    final target = buttonIndex + delta;
    if (target < 0 || target >= _rows[rowIndex].length) return;
    setState(() {
      final button = _rows[rowIndex].removeAt(buttonIndex);
      _rows[rowIndex].insert(target, button);
    });
  }

  void _changeAction(_MenuButton button, String? actionId) {
    if (actionId == null || actionId == button.action) return;
    final duplicated = _rows.any(
      (row) => row.any(
        (item) => item.id != button.id && item.action == actionId,
      ),
    );
    if (duplicated) {
      _toast('هذا الإجراء مستخدم في زر آخر — لا يمكن تكراره.');
      return;
    }
    setState(() {
      button.action = actionId;
      final suggested = _labelFor(actionId);
      if (button.label.trim().isEmpty ||
          _actions.any((item) => item.label == button.label)) {
        button.label = suggested;
      }
    });
  }

  void _toast(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message)));
  }

  String? _validate() {
    if (_rows.isEmpty) return 'أضف زرًا واحدًا على الأقل.';
    if (_rows.length > _maxRows) return 'عدد الصفوف يتجاوز $_maxRows.';
    if (_totalButtons > _maxButtons) return 'عدد الأزرار يتجاوز $_maxButtons.';
    final used = <String>{};
    for (final row in _rows) {
      if (row.length > _maxRowButtons) {
        return 'كل صف يسمح بـ $_maxRowButtons أزرار كحد أقصى.';
      }
      for (final button in row) {
        final label = button.label.trim();
        if (label.isEmpty) return 'لا يمكن ترك نص زر فارغًا.';
        if (label.length > _maxLabel) {
          return 'نص الزر "$label" يتجاوز $_maxLabel حرفًا.';
        }
        if (_actionById(button.action) == null) {
          return 'إجراء غير معروف: ${button.action}';
        }
        if (!used.add(button.action)) {
          return 'الإجراء ${button.action} مكرر في أكثر من زر.';
        }
      }
    }
    return null;
  }

  Future<void> _save() async {
    final problem = _validate();
    if (problem != null) {
      _toast(problem);
      return;
    }
    setState(() => _saving = true);
    try {
      final data = await widget.api.put('/api/admin/bot-buttons', {
        'items': [
          for (final row in _rows)
            [for (final button in row) button.toJson()],
        ],
      });
      if (!mounted) return;
      setState(() {
        _applyServerPayload(data);
        _saving = false;
      });
      _toast('تم الحفظ — الأزرار الجديدة ظاهرة للمستخدمين الآن.');
    } on ApiException catch (exc) {
      if (!mounted) return;
      setState(() => _saving = false);
      _toast(_describe(exc));
    } catch (exc) {
      if (!mounted) return;
      setState(() => _saving = false);
      _toast('تعذر الحفظ: $exc');
    }
  }

  Future<void> _resetToDefault() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('إعادة الأزرار الافتراضية'),
        content: const Text(
          'سيُلغى التخطيط المحفوظ وتعود قائمة البوت كما كانت في الكود. '
          'هل تريد المتابعة؟',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('إلغاء'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('إعادة الافتراضي'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    setState(() => _saving = true);
    try {
      final data = await widget.api.del('/api/admin/bot-buttons', const {});
      if (!mounted) return;
      setState(() {
        _applyServerPayload(data);
        _saving = false;
      });
      _toast('تمت إعادة الأزرار الافتراضية.');
    } on ApiException catch (exc) {
      if (!mounted) return;
      setState(() => _saving = false);
      _toast(_describe(exc));
    } catch (exc) {
      if (!mounted) return;
      setState(() => _saving = false);
      _toast('تعذرت الإعادة: $exc');
    }
  }

  Widget _banner(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: colors.primaryContainer.withValues(alpha: .45),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: colors.primary.withValues(alpha: .35)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.bolt_rounded, color: colors.primary),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'تعديل حي — يُطبَّق على البوت فورًا',
                  style: TextStyle(fontWeight: FontWeight.w800),
                ),
                const SizedBox(height: 3),
                Text(
                  'هذه الأزرار هي قائمة المستخدم الرئيسية التي تظهر بعد /start. '
                  'الحفظ يرسل التخطيط للخادم ويخزّنه في قاعدة البيانات؛ '
                  'يراه المستخدم في الرسالة التالية بدون إعادة نشر.',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
                if (_customized)
                  Padding(
                    padding: const EdgeInsets.only(top: 6),
                    child: Chip(
                      visualDensity: VisualDensity.compact,
                      label: const Text('تخطيط مخصص محفوظ'),
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _toolbar(BuildContext context) {
    final busy = _saving || _loading;
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        FilledButton.icon(
          onPressed: busy ? null : _save,
          icon: _saving
              ? const SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Icon(Icons.save_outlined),
          label: const Text('حفظ وتطبيق على البوت'),
        ),
        OutlinedButton.icon(
          onPressed: busy ? null : () => setState(() => _showPreview = !_showPreview),
          icon: const Icon(Icons.smartphone_outlined),
          label: Text(_showPreview ? 'إخفاء المعاينة' : 'معاينة'),
        ),
        OutlinedButton.icon(
          onPressed: busy ? null : _load,
          icon: const Icon(Icons.refresh_rounded),
          label: const Text('تحديث من الخادم'),
        ),
        TextButton.icon(
          onPressed: busy ? null : _resetToDefault,
          icon: const Icon(Icons.restart_alt),
          label: const Text('إعادة الافتراضي'),
        ),
      ],
    );
  }

  Widget _buttonEditor(BuildContext context, int rowIndex, int buttonIndex) {
    final colors = Theme.of(context).colorScheme;
    final button = _rows[rowIndex][buttonIndex];
    final rowLength = _rows[rowIndex].length;
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: colors.surfaceContainerLow,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: colors.outlineVariant),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: TextFormField(
                  key: ValueKey('menu-label-$rowIndex-$buttonIndex-${button.id}'),
                  initialValue: button.label,
                  maxLength: _maxLabel,
                  decoration: const InputDecoration(
                    labelText: 'نص الزر',
                    isDense: true,
                    border: OutlineInputBorder(),
                    counterText: '',
                  ),
                  onChanged: (value) => button.label = value,
                ),
              ),
              const SizedBox(width: 6),
              IconButton(
                tooltip: 'أعلى',
                visualDensity: VisualDensity.compact,
                onPressed: buttonIndex == 0
                    ? null
                    : () => _moveButton(rowIndex, buttonIndex, -1),
                icon: const Icon(Icons.arrow_upward, size: 18),
              ),
              IconButton(
                tooltip: 'أسفل',
                visualDensity: VisualDensity.compact,
                onPressed: buttonIndex == rowLength - 1
                    ? null
                    : () => _moveButton(rowIndex, buttonIndex, 1),
                icon: const Icon(Icons.arrow_downward, size: 18),
              ),
              IconButton(
                tooltip: 'حذف الزر',
                visualDensity: VisualDensity.compact,
                onPressed: () => _removeButton(rowIndex, buttonIndex),
                icon: const Icon(Icons.delete_outline, size: 19),
              ),
            ],
          ),
          const SizedBox(height: 6),
          DropdownButtonFormField<String>(
            key: ValueKey('menu-action-$rowIndex-$buttonIndex-${button.id}'),
            isExpanded: true,
            initialValue: _actionById(button.action) == null
                ? null
                : button.action,
            decoration: const InputDecoration(
              labelText: 'الإجراء عند الضغط',
              isDense: true,
              border: OutlineInputBorder(),
            ),
            items: [
              for (final action in _actions)
                DropdownMenuItem(
                  value: action.id,
                  child: Text(action.label, overflow: TextOverflow.ellipsis),
                ),
            ],
            onChanged: (value) => _changeAction(button, value),
          ),
        ],
      ),
    );
  }

  Widget _rowCard(BuildContext context, int rowIndex) {
    final colors = Theme.of(context).colorScheme;
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: BorderSide(color: colors.outlineVariant),
      ),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                CircleAvatar(
                  radius: 14,
                  backgroundColor: colors.primaryContainer,
                  child: Text(
                    '${rowIndex + 1}',
                    style: const TextStyle(fontSize: 12),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    'صف ${rowIndex + 1} — ${_rows[rowIndex].length} أزرار',
                    style: Theme.of(context)
                        .textTheme
                        .titleSmall
                        ?.copyWith(fontWeight: FontWeight.w700),
                  ),
                ),
                IconButton(
                  tooltip: 'رفع الصف',
                  visualDensity: VisualDensity.compact,
                  onPressed: rowIndex == 0
                      ? null
                      : () => _moveRow(rowIndex, -1),
                  icon: const Icon(Icons.keyboard_arrow_up),
                ),
                IconButton(
                  tooltip: 'إنزال الصف',
                  visualDensity: VisualDensity.compact,
                  onPressed: rowIndex == _rows.length - 1
                      ? null
                      : () => _moveRow(rowIndex, 1),
                  icon: const Icon(Icons.keyboard_arrow_down),
                ),
                IconButton(
                  tooltip: 'حذف الصف',
                  visualDensity: VisualDensity.compact,
                  onPressed: () => _removeRow(rowIndex),
                  icon: const Icon(Icons.delete_sweep_outlined),
                ),
              ],
            ),
            const SizedBox(height: 8),
            for (var i = 0; i < _rows[rowIndex].length; i++)
              _buttonEditor(context, rowIndex, i),
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: TextButton.icon(
                onPressed: () => _addButton(rowIndex),
                icon: const Icon(Icons.add, size: 18),
                label: const Text('إضافة زر لهذا الصف'),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _previewCard(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Card(
      color: colors.surfaceContainerLow,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(24),
        side: BorderSide(color: colors.outlineVariant, width: 2),
      ),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                CircleAvatar(
                  radius: 16,
                  backgroundColor: colors.primaryContainer,
                  child: const Icon(Icons.smart_toy_outlined, size: 18),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    'معاينة قائمة البوت',
                    style: Theme.of(context)
                        .textTheme
                        .titleSmall
                        ?.copyWith(fontWeight: FontWeight.w800),
                  ),
                ),
              ],
            ),
            const Divider(),
            for (final row in _rows)
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(
                  children: [
                    for (var i = 0; i < row.length; i++) ...[
                      if (i > 0) const SizedBox(width: 6),
                      Expanded(
                        child: Container(
                          padding: const EdgeInsets.symmetric(
                            horizontal: 8,
                            vertical: 10,
                          ),
                          decoration: BoxDecoration(
                            color: colors.primary.withValues(alpha: .08),
                            borderRadius: BorderRadius.circular(10),
                            border: Border.all(
                              color: colors.primary.withValues(alpha: .3),
                            ),
                          ),
                          child: Text(
                            row[i].label,
                            textAlign: TextAlign.center,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: Theme.of(context).textTheme.bodySmall,
                          ),
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            const SizedBox(height: 4),
            Text(
              'هكذا ستظهر الأزرار للمستخدم في تيليجرام بعد الحفظ.',
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.labelSmall,
            ),
          ],
        ),
      ),
    );
  }

  Widget _body(BuildContext context) {
    if (_loading) {
      return const Padding(
        padding: EdgeInsets.all(40),
        child: Center(child: CircularProgressIndicator()),
      );
    }
    if (_error != null) {
      return Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(_error!, style: const TextStyle(fontWeight: FontWeight.w700)),
              const SizedBox(height: 10),
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: OutlinedButton.icon(
                  onPressed: _load,
                  icon: const Icon(Icons.refresh_rounded),
                  label: const Text('إعادة المحاولة'),
                ),
              ),
            ],
          ),
        ),
      );
    }
    final width = MediaQuery.sizeOf(context).width;
    final editor = Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (var i = 0; i < _rows.length; i++) _rowCard(context, i),
        OutlinedButton.icon(
          onPressed: _addRow,
          icon: const Icon(Icons.add_box_outlined),
          label: const Text('إضافة صف جديد'),
        ),
      ],
    );
    if (!_showPreview) return editor;
    if (width >= 900) {
      return Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(flex: 6, child: editor),
          const SizedBox(width: 14),
          Expanded(flex: 4, child: _previewCard(context)),
        ],
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _previewCard(context),
        const SizedBox(height: 14),
        editor,
      ],
    );
  }

  @override
  Widget build(BuildContext context) => ListView(
    padding: const EdgeInsets.all(16),
    children: [
      _banner(context),
      const SizedBox(height: 12),
      _toolbar(context),
      const SizedBox(height: 14),
      _body(context),
    ],
  );
}
