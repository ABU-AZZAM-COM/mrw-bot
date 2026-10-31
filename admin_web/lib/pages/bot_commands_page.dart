import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';

class _CommandItem {
  _CommandItem({required this.command, required this.description});

  String command;
  String description;

  Map<String, dynamic> toJson() => {
    'command': command.trim().toLowerCase(),
    'description': description.trim(),
  };
}

class _ScopeItem {
  _ScopeItem({
    required this.scope,
    this.chatId,
    required this.language,
    required this.commands,
  });

  String scope;
  int? chatId;
  String language;
  final List<_CommandItem> commands;

  bool get needsChat => scope == 'chat' || scope == 'chat_administrators';

  Map<String, dynamic> toJson() => {
    'scope': scope,
    'chat_id': needsChat ? chatId : null,
    'language': language.trim(),
    'commands': [for (final item in commands) item.toJson()],
  };
}

/// محرر أوامر البوت ونطاقاتها ولغاتها.
///
/// كل حفظ يدفع الأوامر إلى تيليجرام مباشرة؛ النطاقات المحذوفة تُحذف من
/// تيليجرام أيضًا. الأوامر لا تظهر للمستخدم إلا في قائمة الأوامر (☰).
class BotCommandsPage extends StatefulWidget {
  const BotCommandsPage({super.key, required this.api});

  final AdminApi api;

  @override
  State<BotCommandsPage> createState() => _BotCommandsPageState();
}

class _BotCommandsPageState extends State<BotCommandsPage> {
  static const _maxCommands = 100;

  final List<_ScopeItem> _scopes = [];
  List<Map<String, String>> _scopeOptions = const [
    {'id': 'default', 'label': 'الافتراضي (كل المحادثات)'},
  ];
  Map<String, dynamic> _defaults = const {};
  bool _loading = true;
  bool _saving = false;
  bool _customized = false;
  bool _botReady = false;
  String? _error;
  List<String> _applyErrors = const [];

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
      final data = await widget.api.get('/api/admin/bot-commands');
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
    final scopes = data['scopes'];
    if (scopes is List && scopes.isNotEmpty) {
      _scopeOptions = [
        for (final item in scopes)
          if (item is Map)
            {'id': '${item['id']}', 'label': '${item['label']}'},
      ];
    }
    _defaults = data['defaults'] is Map
        ? (data['defaults'] as Map).cast<String, dynamic>()
        : const {};
    final items = data['items'];
    _scopes
      ..clear()
      ..addAll(_parseScopes(items));
  }

  List<_ScopeItem> _parseScopes(Object? raw) {
    final result = <_ScopeItem>[];
    if (raw is! Map) return result;
    final scopes = raw['scopes'];
    if (scopes is! List) return result;
    for (final item in scopes) {
      if (item is! Map) continue;
      final commands = <_CommandItem>[];
      final commandsRaw = item['commands'];
      if (commandsRaw is List) {
        for (final command in commandsRaw) {
          if (command is! Map) continue;
          commands.add(
            _CommandItem(
              command: '${command['command'] ?? ''}',
              description: '${command['description'] ?? ''}',
            ),
          );
        }
      }
      result.add(
        _ScopeItem(
          scope: '${item['scope'] ?? 'default'}',
          chatId: item['chat_id'] is num
              ? (item['chat_id'] as num).toInt()
              : null,
          language: '${item['language'] ?? ''}',
          commands: commands,
        ),
      );
    }
    return result;
  }

  String? _validate() {
    if (_scopes.isEmpty) return 'أضف نطاقًا واحدًا على الأقل.';
    final keys = <String>{};
    for (final scope in _scopes) {
      if (scope.needsChat && (scope.chatId == null || scope.chatId == 0)) {
        return 'نطاق «${_scopeLabel(scope.scope)}» يحتاج معرّف محادثة صحيحًا.';
      }
      if (scope.commands.isEmpty) {
        return 'نطاق «${_scopeLabel(scope.scope)}» بلا أوامر.';
      }
      if (scope.commands.length > _maxCommands) {
        return 'الحد الأقصى $_maxCommands أمر في النطاق الواحد.';
      }
      final key =
          '${scope.scope}|${scope.needsChat ? scope.chatId : ''}|'
          '${scope.language.trim().toLowerCase()}';
      if (!keys.add(key)) {
        return 'نطاق مكرر: ${_scopeLabel(scope.scope)} / ${scope.language}';
      }
      final commands = <String>{};
      for (final item in scope.commands) {
        final command = item.command.trim().toLowerCase();
        if (!RegExp(r'^[a-z0-9_]{1,32}$').hasMatch(command)) {
          return 'أمر غير صالح: «${item.command}» — حروف إنجليزية صغيرة وأرقام و _ فقط.';
        }
        if (!commands.add(command)) {
          return 'الأمر /$command مكرر داخل النطاق.';
        }
        final description = item.description.trim();
        if (description.isEmpty || description.length > 256) {
          return 'وصف /$command مطلوب وبحد أقصى 256 حرفًا.';
        }
      }
    }
    return null;
  }

  Future<void> _save({Map<String, dynamic>? payload}) async {
    final body = payload ?? {
      'items': {'scopes': [for (final scope in _scopes) scope.toJson()]},
    };
    setState(() {
      _saving = true;
      _applyErrors = const [];
    });
    try {
      final data = await widget.api.put('/api/admin/bot-commands', body);
      if (!mounted) return;
      setState(() {
        _applyServerPayload(data);
        final applied = data['applied'];
        if (applied is Map) {
          final errors = applied['errors'];
          _applyErrors = errors is List ? [for (final item in errors) '$item'] : const [];
        }
        _saving = false;
      });
      if (_applyErrors.isEmpty) {
        toast(context, 'تم حفظ الأوامر وتطبيقها على تيليجرام.');
      } else {
        toast(
          context,
          'حُفظت الأوامر، لكن ${_applyErrors.length} نطاقًا رفضته تيليجرام.',
          error: true,
        );
      }
    } catch (exc) {
      if (!mounted) return;
      setState(() => _saving = false);
      toast(context, errMsg(exc), error: true);
    }
  }

  Future<void> _resetToDefault() async {
    if (_defaults.isEmpty) {
      toast(context, 'تعذر تحميل الأوامر الافتراضية — حدّث الصفحة.', error: true);
      return;
    }
    final confirmed = await confirmDialog(
      context,
      'استعادة الأوامر الافتراضية',
      'ستُستبدل كل النطاقات بالأوامر الافتراضية للبوت. هل تريد المتابعة؟',
      okLabel: 'استعادة',
    );
    if (!confirmed) return;
    await _save(payload: {'items': _defaults});
  }

  void _addScope() {
    final used = {for (final scope in _scopes) scope.scope};
    var scope = _scopeOptions.first['id'] ?? 'default';
    for (final option in _scopeOptions) {
      if (!used.contains(option['id'])) {
        scope = option['id'] ?? scope;
        break;
      }
    }
    setState(() {
      _scopes.add(
        _ScopeItem(
          scope: scope,
          chatId: null,
          language: '',
          commands: [_CommandItem(command: 'start', description: 'بدء الاستخدام')],
        ),
      );
    });
  }

  String _scopeLabel(String id) {
    for (final option in _scopeOptions) {
      if (option['id'] == id) return option['label'] ?? id;
    }
    return id;
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
              Icon(Icons.terminal_rounded, color: colors.primary),
              const SizedBox(width: 8),
              const Expanded(
                child: Text(
                  'أوامر البوت — نطاقات ولغات',
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
            'النطاق «الافتراضي» يظهر للجميع، ونطاق «محادثة محددة» يخص مشرفًا '
            'أو محادثة بعينها. الحذف هنا يحذف الأمر من تيليجرام أيضًا.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          if (!_botReady)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text(
                'البوت غير جاهز — سيُخزَّن الحفظ ويُطبَّق عند الإقلاع التالي.',
                style: TextStyle(color: colors.error, fontSize: 12),
              ),
            ),
          if (_applyErrors.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Container(
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                  color: colors.errorContainer.withValues(alpha: .4),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    for (final error in _applyErrors.take(5))
                      Text('• $error', style: const TextStyle(fontSize: 11)),
                  ],
                ),
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
          onPressed: busy ? null : () => _save(),
          icon: const Icon(Icons.save_outlined),
          label: const Text('حفظ وتطبيق'),
        ),
        OutlinedButton.icon(
          onPressed: busy ? null : _addScope,
          icon: const Icon(Icons.add),
          label: const Text('إضافة نطاق'),
        ),
        OutlinedButton.icon(
          onPressed: busy ? null : _load,
          icon: const Icon(Icons.refresh_rounded),
          label: const Text('تحديث'),
        ),
        TextButton.icon(
          onPressed: busy ? null : _resetToDefault,
          icon: const Icon(Icons.restart_alt),
          label: const Text('استعادة الافتراضي'),
        ),
      ],
    );
  }

  Widget _scopeCard(BuildContext context, int index) {
    final colors = Theme.of(context).colorScheme;
    final scope = _scopes[index];
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
                  child: Text('${index + 1}', style: const TextStyle(fontSize: 12)),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    '${_scopeLabel(scope.scope)} — ${scope.commands.length} أمر',
                    style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13),
                  ),
                ),
                IconButton(
                  tooltip: 'حذف النطاق',
                  onPressed: () => setState(() => _scopes.removeAt(index)),
                  icon: const Icon(Icons.delete_sweep_outlined),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                SizedBox(
                  width: 220,
                  child: DropdownButtonFormField<String>(
                    key: ValueKey('scope-type-$index'),
                    isExpanded: true,
                    initialValue: _scopeOptions.any(
                      (option) => option['id'] == scope.scope,
                    )
                        ? scope.scope
                        : 'default',
                    decoration: const InputDecoration(
                      labelText: 'النطاق',
                      isDense: true,
                      border: OutlineInputBorder(),
                    ),
                    items: [
                      for (final option in _scopeOptions)
                        DropdownMenuItem(
                          value: option['id'],
                          child: Text(
                            option['label'] ?? option['id'] ?? '',
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                    ],
                    onChanged: (value) => setState(
                      () => scope.scope = value ?? 'default',
                    ),
                  ),
                ),
                if (scope.needsChat)
                  SizedBox(
                    width: 180,
                    child: TextFormField(
                      key: ValueKey('scope-chat-$index'),
                      initialValue: scope.chatId?.toString() ?? '',
                      keyboardType: TextInputType.number,
                      decoration: const InputDecoration(
                        labelText: 'معرّف المحادثة',
                        isDense: true,
                        border: OutlineInputBorder(),
                      ),
                      onChanged: (value) =>
                          scope.chatId = int.tryParse(value.trim()),
                    ),
                  ),
                SizedBox(
                  width: 140,
                  child: TextFormField(
                    key: ValueKey('scope-lang-$index'),
                    initialValue: scope.language,
                    decoration: const InputDecoration(
                      labelText: 'اللغة (ar/en…)',
                      helperText: 'فارغ = كل اللغات',
                      isDense: true,
                      border: OutlineInputBorder(),
                    ),
                    onChanged: (value) => scope.language = value,
                  ),
                ),
              ],
            ),
            const Divider(height: 18),
            for (var commandIndex = 0;
                commandIndex < scope.commands.length;
                commandIndex++)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SizedBox(
                      width: 150,
                      child: TextFormField(
                        key: ValueKey('cmd-name-$index-$commandIndex'),
                        initialValue: scope.commands[commandIndex].command,
                        maxLength: 32,
                        decoration: const InputDecoration(
                          labelText: 'الأمر',
                          prefixText: '/',
                          isDense: true,
                          border: OutlineInputBorder(),
                          counterText: '',
                        ),
                        onChanged: (value) =>
                            scope.commands[commandIndex].command = value,
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: TextFormField(
                        key: ValueKey('cmd-desc-$index-$commandIndex'),
                        initialValue: scope.commands[commandIndex].description,
                        maxLength: 256,
                        decoration: const InputDecoration(
                          labelText: 'الوصف',
                          isDense: true,
                          border: OutlineInputBorder(),
                          counterText: '',
                        ),
                        onChanged: (value) =>
                            scope.commands[commandIndex].description = value,
                      ),
                    ),
                    IconButton(
                      tooltip: 'حذف الأمر',
                      onPressed: () => setState(
                        () => scope.commands.removeAt(commandIndex),
                      ),
                      icon: const Icon(Icons.close, size: 18),
                    ),
                  ],
                ),
              ),
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: TextButton.icon(
                onPressed: () => setState(
                  () => scope.commands.add(
                    _CommandItem(command: '', description: ''),
                  ),
                ),
                icon: const Icon(Icons.add, size: 18),
                label: const Text('إضافة أمر'),
              ),
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
    if (_error != null) return ErrorView(_error!, _load);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (var i = 0; i < _scopes.length; i++) _scopeCard(context, i),
        if (_scopes.isEmpty)
          const EmptyView('لا نطاقات — أضف نطاقًا ليظهر أمر /start للجميع.'),
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
