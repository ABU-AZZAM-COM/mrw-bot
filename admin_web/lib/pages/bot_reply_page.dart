import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';

class _ReplyButton {
  _ReplyButton({
    required this.text,
    required this.type,
    this.style,
    this.icon,
  });

  String text;
  String type;
  String? style;
  String? icon;

  Map<String, dynamic> toJson() {
    final data = <String, dynamic>{'text': text, 'type': type};
    if (style != null && style!.isNotEmpty) data['style'] = style;
    if (icon != null && icon!.isNotEmpty) data['icon'] = icon;
    return data;
  }
}

class _ReplyKeyboard {
  _ReplyKeyboard({
    required this.id,
    required this.label,
    required this.placeholder,
    required this.persistent,
    required this.rows,
  });

  final String id;
  final String label;
  String placeholder;
  bool persistent;
  final List<List<_ReplyButton>> rows;

  Map<String, dynamic> toJson() => {
    'placeholder': placeholder,
    'persistent': persistent,
    'rows': [
      for (final row in rows) [for (final button in row) button.toJson()],
    ],
  };
}

/// محرر لوحات الرد (الأزرار الثابتة أسفل المحادثة).
///
/// الحفظ يطبّق فورًا على البوت: بناء اللوحات يقرأ الإعداد المحفوظ في كل إرسال،
/// كما أن مطابقة نصوص الأزرار (إلغاء/تأكيد/تخطي/رجوع) تصبح دلالية حسب النص
/// الجديد، فلا تتعطل أي خطوة عند إعادة تسمية الأزرار.
class BotReplyPage extends StatefulWidget {
  const BotReplyPage({super.key, required this.api});

  final AdminApi api;

  @override
  State<BotReplyPage> createState() => _BotReplyPageState();
}

class _BotReplyPageState extends State<BotReplyPage> {
  static const _maxRows = 6;
  static const _maxRowButtons = 4;
  static const _maxLabel = 64;

  final List<_ReplyKeyboard> _keyboards = [];
  List<String> _types = const [
    'text',
    'cancel',
    'confirm',
    'contact',
    'location',
    'skip',
    'back',
    'web_app',
  ];
  List<String> _styles = const ['primary', 'success', 'danger'];
  bool _loading = true;
  bool _saving = false;
  bool _customized = false;
  bool _botReady = false;
  String _miniAppUrl = '';
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final data = await widget.api.get('/api/admin/bot-reply-keyboards');
      if (!mounted) return;
      setState(() {
        _applyServerPayload(data);
        _loading = false;
      });
    } catch (exc) {
      if (!mounted) return;
      setState(() {
        _error = errMsg(exc);
        _loading = false;
      });
    }
  }

  void _applyServerPayload(Map<String, dynamic> data) {
    _customized = data['customized'] == true;
    _botReady = data['bot_ready'] == true;
    _miniAppUrl = '${data['mini_app_url'] ?? ''}';
    final types = data['types'];
    if (types is List && types.isNotEmpty) {
      _types = [for (final value in types) '$value'];
    }
    final styles = data['styles'];
    if (styles is List && styles.isNotEmpty) {
      _styles = [for (final value in styles) '$value'];
    }
    final ids = <Map<String, String>>[];
    final idsRaw = data['ids'];
    if (idsRaw is List) {
      for (final item in idsRaw) {
        if (item is Map) {
          ids.add({
            'id': '${item['id']}',
            'label': '${item['label']}',
          });
        }
      }
    }
    final items = data['items'];
    _keyboards
      ..clear()
      ..addAll(_parseKeyboards(items, ids));
  }

  List<_ReplyKeyboard> _parseKeyboards(Object? raw, List<Map<String, String>> ids) {
    final result = <_ReplyKeyboard>[];
    if (raw is! Map) return result;
    final known = ids.isEmpty
        ? [for (final key in raw.keys) {'id': '$key', 'label': '$key'}]
        : ids;
    for (final entry in known) {
      final id = entry['id'] ?? '';
      final config = raw[id];
      if (config is! Map) continue;
      final rows = <List<_ReplyButton>>[];
      final rowsRaw = config['rows'];
      if (rowsRaw is List) {
        for (final rowRaw in rowsRaw) {
          if (rowRaw is! List) continue;
          final row = <_ReplyButton>[];
          for (final item in rowRaw) {
            if (item is! Map) continue;
            final text = '${item['text'] ?? ''}'.trim();
            if (text.isEmpty) continue;
            row.add(
              _ReplyButton(
                text: text,
                type: '${item['type'] ?? 'text'}',
                style: item['style'] is String && '${item['style']}'.isNotEmpty
                    ? '${item['style']}'
                    : null,
                icon: item['icon'] is String && '${item['icon']}'.isNotEmpty
                    ? '${item['icon']}'
                    : null,
              ),
            );
          }
          if (row.isNotEmpty) rows.add(row);
        }
      }
      result.add(
        _ReplyKeyboard(
          id: id,
          label: entry['label'] ?? id,
          placeholder: '${config['placeholder'] ?? ''}',
          persistent: config['persistent'] == true,
          rows: rows,
        ),
      );
    }
    return result;
  }

  String? _validate() {
    for (final keyboard in _keyboards) {
      if (keyboard.rows.isEmpty) {
        return 'لوحة «${keyboard.label}» بلا أزرار — أضف زرًا واحدًا على الأقل.';
      }
      if (keyboard.rows.length > _maxRows) {
        return 'لوحة «${keyboard.label}» تتجاوز $_maxRows صفوف.';
      }
      for (final row in keyboard.rows) {
        if (row.length > _maxRowButtons) {
          return 'لوحة «${keyboard.label}»: كل صف يسمح بـ $_maxRowButtons أزرار.';
        }
        for (final button in row) {
          final text = button.text.trim();
          if (text.isEmpty) {
            return 'لوحة «${keyboard.label}»: لا يمكن ترك نص زر فارغًا.';
          }
          if (text.length > _maxLabel) {
            return 'نص الزر «$text» يتجاوز $_maxLabel حرفًا.';
          }
          if (button.type == 'web_app' && _miniAppUrl.isEmpty) {
            return 'زر تطبيق الويب يحتاج رابط Mini App — اضبطه في تبويب الإعدادات.';
          }
        }
      }
    }
    return null;
  }

  Future<void> _save() async {
    final problem = _validate();
    if (problem != null) {
      toast(context, problem, error: true);
      return;
    }
    setState(() => _saving = true);
    try {
      final data = await widget.api.put('/api/admin/bot-reply-keyboards', {
        'items': {
          for (final keyboard in _keyboards) keyboard.id: keyboard.toJson(),
        },
      });
      if (!mounted) return;
      setState(() {
        _applyServerPayload(data);
        _saving = false;
      });
      toast(context, 'تم الحفظ — لوحات الرد الجديدة تظهر مع الرسالة التالية.');
    } catch (exc) {
      if (!mounted) return;
      setState(() => _saving = false);
      toast(context, errMsg(exc), error: true);
    }
  }

  Future<void> _reset() async {
    final confirmed = await confirmDialog(
      context,
      'إعادة لوحات الرد الافتراضية',
      'سيُلغى التخصيص المحفوظ وتعود اللوحات كما كانت في الكود. هل تريد المتابعة؟',
      okLabel: 'إعادة الافتراضي',
    );
    if (!confirmed) return;
    setState(() => _saving = true);
    try {
      final data = await widget.api.del(
        '/api/admin/bot-reply-keyboards',
        const {},
      );
      if (!mounted) return;
      setState(() {
        _applyServerPayload(data);
        _saving = false;
      });
      toast(context, 'تمت إعادة لوحات الرد الافتراضية.');
    } catch (exc) {
      if (!mounted) return;
      setState(() => _saving = false);
      toast(context, errMsg(exc), error: true);
    }
  }

  void _addRow(_ReplyKeyboard keyboard) {
    if (keyboard.rows.length >= _maxRows) {
      toast(context, 'وصلت للحد الأقصى من الصفوف ($_maxRows).', error: true);
      return;
    }
    setState(() {
      keyboard.rows.add([
        _ReplyButton(text: 'زر جديد', type: _types.contains('text') ? 'text' : _types.first),
      ]);
    });
  }

  void _addButton(_ReplyKeyboard keyboard, int rowIndex) {
    if (keyboard.rows[rowIndex].length >= _maxRowButtons) {
      toast(
        context,
        'لا يمكن وضع أكثر من $_maxRowButtons أزرار في صف واحد.',
        error: true,
      );
      return;
    }
    setState(() {
      keyboard.rows[rowIndex].add(
        _ReplyButton(text: 'زر', type: _types.contains('text') ? 'text' : _types.first),
      );
    });
  }

  void _removeButton(_ReplyKeyboard keyboard, int rowIndex, int buttonIndex) {
    setState(() {
      keyboard.rows[rowIndex].removeAt(buttonIndex);
      if (keyboard.rows[rowIndex].isEmpty) keyboard.rows.removeAt(rowIndex);
    });
  }

  static String _typeLabel(String type) {
    switch (type) {
      case 'text':
        return 'نص عادي';
      case 'cancel':
        return 'إلغاء';
      case 'confirm':
        return 'تأكيد الإرسال';
      case 'contact':
        return 'مشاركة جهة اتصال';
      case 'location':
        return 'مشاركة موقع';
      case 'skip':
        return 'تخطي';
      case 'back':
        return 'رجوع';
      case 'web_app':
        return 'تطبيق ويب';
      default:
        return type;
    }
  }

  static String _styleLabel(String style) {
    switch (style) {
      case 'primary':
        return 'أزرق';
      case 'success':
        return 'أخضر';
      case 'danger':
        return 'أحمر';
      default:
        return style;
    }
  }

  Widget _banner(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: colors.primaryContainer.withValues(alpha: .4),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: colors.primary.withValues(alpha: .3)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.keyboard_alt_outlined, color: colors.primary),
              const SizedBox(width: 8),
              const Expanded(
                child: Text(
                  'لوحات الرد — تُطبَّق فورًا',
                  style: TextStyle(fontWeight: FontWeight.w800),
                ),
              ),
              if (_customized)
                const Chip(
                  visualDensity: VisualDensity.compact,
                  label: Text('مخصصة'),
                ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            'تعديل نص زر «إلغاء» أو «تأكيد» أو «تخطي» أو «رجوع» آمن: '
            'البوت يتعرّف على الأزرار حسب نوعها لا حسب نصها. '
            'لوحة تطبيق الويب تحتاج رابط Mini App مضبوطًا في تبويب الإعدادات.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          if (!_botReady)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text(
                'البوت غير جاهز الآن — الحفظ يُخزَّن ويُطبَّق عند عودته.',
                style: TextStyle(color: colors.error, fontSize: 12),
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
      children: [
        FilledButton.icon(
          onPressed: busy ? null : _save,
          icon: const Icon(Icons.save_outlined),
          label: const Text('حفظ وتطبيق'),
        ),
        OutlinedButton.icon(
          onPressed: busy ? null : _load,
          icon: const Icon(Icons.refresh_rounded),
          label: const Text('تحديث'),
        ),
        TextButton.icon(
          onPressed: busy ? null : _reset,
          icon: const Icon(Icons.restart_alt),
          label: const Text('إعادة الافتراضي'),
        ),
      ],
    );
  }

  Widget _buttonEditor(_ReplyKeyboard keyboard, int rowIndex, int buttonIndex) {
    final colors = Theme.of(context).colorScheme;
    final button = keyboard.rows[rowIndex][buttonIndex];
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
            children: [
              Expanded(
                child: TextFormField(
                  key: ValueKey(
                    'reply-text-${keyboard.id}-$rowIndex-$buttonIndex',
                  ),
                  initialValue: button.text,
                  maxLength: _maxLabel,
                  decoration: const InputDecoration(
                    labelText: 'نص الزر',
                    isDense: true,
                    border: OutlineInputBorder(),
                    counterText: '',
                  ),
                  onChanged: (value) => button.text = value,
                ),
              ),
              IconButton(
                tooltip: 'حذف الزر',
                onPressed: () => _removeButton(keyboard, rowIndex, buttonIndex),
                icon: const Icon(Icons.delete_outline, size: 19),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              SizedBox(
                width: 170,
                child: DropdownButtonFormField<String>(
                  key: ValueKey(
                    'reply-type-${keyboard.id}-$rowIndex-$buttonIndex',
                  ),
                  isExpanded: true,
                  initialValue: _types.contains(button.type)
                      ? button.type
                      : 'text',
                  decoration: const InputDecoration(
                    labelText: 'نوع الزر',
                    isDense: true,
                    border: OutlineInputBorder(),
                  ),
                  items: [
                    for (final type in _types)
                      DropdownMenuItem(value: type, child: Text(_typeLabel(type))),
                  ],
                  onChanged: (value) =>
                      setState(() => button.type = value ?? 'text'),
                ),
              ),
              SizedBox(
                width: 120,
                child: DropdownButtonFormField<String?>(
                  key: ValueKey(
                    'reply-style-${keyboard.id}-$rowIndex-$buttonIndex',
                  ),
                  isExpanded: true,
                  initialValue:
                      button.style != null && _styles.contains(button.style)
                      ? button.style
                      : null,
                  decoration: const InputDecoration(
                    labelText: 'اللون',
                    isDense: true,
                    border: OutlineInputBorder(),
                  ),
                  items: [
                    const DropdownMenuItem<String?>(
                      value: null,
                      child: Text('بلا لون'),
                    ),
                    for (final style in _styles)
                      DropdownMenuItem<String?>(
                        value: style,
                        child: Text(_styleLabel(style)),
                      ),
                  ],
                  onChanged: (value) => setState(() => button.style = value),
                ),
              ),
              SizedBox(
                width: 160,
                child: TextFormField(
                  key: ValueKey(
                    'reply-icon-${keyboard.id}-$rowIndex-$buttonIndex',
                  ),
                  initialValue: button.icon ?? '',
                  maxLength: 32,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                    labelText: 'إيموجي مخصص',
                    isDense: true,
                    border: OutlineInputBorder(),
                    counterText: '',
                  ),
                  onChanged: (value) =>
                      button.icon = value.trim().isEmpty ? null : value.trim(),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _keyboardCard(BuildContext context, _ReplyKeyboard keyboard) {
    final colors = Theme.of(context).colorScheme;
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: BorderSide(color: colors.outlineVariant),
      ),
      child: ExpansionTile(
        tilePadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
        childrenPadding: const EdgeInsets.fromLTRB(14, 0, 14, 14),
        leading: CircleAvatar(
          radius: 16,
          backgroundColor: colors.primaryContainer,
          child: const Icon(Icons.keyboard_outlined, size: 17),
        ),
        title: Text(
          keyboard.label,
          style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14),
        ),
        subtitle: Text(
          '${keyboard.rows.fold<int>(0, (sum, row) => sum + row.length)} أزرار '
          '• ${keyboard.rows.length} صفوف',
          style: const TextStyle(fontSize: 11),
        ),
        children: [
          Row(
            children: [
              Expanded(
                child: TextFormField(
                  key: ValueKey('reply-placeholder-${keyboard.id}'),
                  initialValue: keyboard.placeholder,
                  maxLength: 120,
                  decoration: const InputDecoration(
                    labelText: 'نص حقل الإدخال (اختياري)',
                    isDense: true,
                    border: OutlineInputBorder(),
                    counterText: '',
                  ),
                  onChanged: (value) => keyboard.placeholder = value,
                ),
              ),
              const SizedBox(width: 12),
              Column(
                children: [
                  const Text('ثابتة', style: TextStyle(fontSize: 12)),
                  Switch(
                    value: keyboard.persistent,
                    onChanged: (value) =>
                        setState(() => keyboard.persistent = value),
                  ),
                ],
              ),
            ],
          ),
          const SizedBox(height: 8),
          for (var rowIndex = 0; rowIndex < keyboard.rows.length; rowIndex++) ...[
            Row(
              children: [
                Text(
                  'صف ${rowIndex + 1}',
                  style: const TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const Spacer(),
                TextButton.icon(
                  onPressed: () => _addButton(keyboard, rowIndex),
                  icon: const Icon(Icons.add, size: 16),
                  label: const Text('زر'),
                ),
              ],
            ),
            for (var buttonIndex = 0;
                buttonIndex < keyboard.rows[rowIndex].length;
                buttonIndex++)
              _buttonEditor(keyboard, rowIndex, buttonIndex),
          ],
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: OutlinedButton.icon(
              onPressed: () => _addRow(keyboard),
              icon: const Icon(Icons.add_box_outlined, size: 18),
              label: const Text('إضافة صف'),
            ),
          ),
        ],
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
      return ErrorView(_error!, _load);
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [for (final keyboard in _keyboards) _keyboardCard(context, keyboard)],
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
