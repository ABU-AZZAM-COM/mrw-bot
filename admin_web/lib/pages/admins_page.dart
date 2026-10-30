import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api.dart';
import '../widgets.dart';

class AdminsPage extends StatefulWidget {
  const AdminsPage({super.key, required this.api, this.onUseOwnerToken});

  final AdminApi api;
  final ValueChanged<String>? onUseOwnerToken;

  @override
  State<AdminsPage> createState() => _AdminsPageState();
}

class _AdminsPageState extends State<AdminsPage> {
  static const _roles = <String, (String, String)>{
    'viewer': ('مشاهد', 'عرض البيانات دون تعديل'),
    'reviewer': ('مراجع', 'عرض البلاغات ومراجعتها'),
    'operator': ('مشغّل', 'مراجعة وتشغيل وإدارة المستخدمين والتصدير'),
    'owner': ('مالك', 'تحكم كامل وإدارة المشرفين والرموز'),
  };
  static const _permissionLabels = <String, String>{
    'read': 'عرض البيانات',
    'review': 'مراجعة البلاغات',
    'users': 'إدارة المبلّغين والمقيّدين',
    'operate': 'إجراءات التشغيل والرسائل والتصنيفات',
    'export': 'التصدير والنسخ الاحتياطية',
    'delete': 'حذف البلاغات',
    'identity': 'مراجعة المراسلين والوصول إلى وثائق الهوية (حساس)',
  };
  static const _rolePermissions = <String, Set<String>>{
    'viewer': {'read'},
    'reviewer': {'read', 'review'},
    'operator': {'read', 'review', 'users', 'operate', 'export'},
    'owner': {
      'read',
      'review',
      'users',
      'operate',
      'export',
      'delete',
      'identity',
    },
  };

  List<Map<String, dynamic>> _items = [];
  bool _loading = true;
  bool _busy = false;
  String? _error;
  bool _bootstrapDisabled = false;

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
      final result = await widget.api.get('/api/admin/admins');
      if (!mounted) return;
      setState(() {
        _items = (result['items'] as List<dynamic>? ?? [])
            .whereType<Map<String, dynamic>>()
            .toList();
        _bootstrapDisabled = result['bootstrap_disabled'] == true;
        _loading = false;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = errMsg(error);
        _loading = false;
      });
    }
  }

  Future<void> _create() async {
    final form = await _adminForm();
    if (form == null || !mounted) return;
    setState(() => _busy = true);
    try {
      await widget.api.post('/api/admin/admins', form);
      if (!mounted) return;
      toast(
        context,
        'أُنشئ حساب المشرف؛ سجّل الدخول باسمه وكلمة المرور المحددة',
      );
      await _load();
    } catch (error) {
      if (mounted) toast(context, _friendlyError(error), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<Map<String, dynamic>?> _adminForm({
    String initialName = '',
    String initialRole = 'reviewer',
    Set<String>? initialPermissions,
    int? initialAdminId,
    bool editing = false,
    bool initialActive = true,
  }) async {
    final name = TextEditingController(text: initialName);
    final telegramId = TextEditingController(
      text: initialAdminId == null || initialAdminId == 0
          ? ''
          : '$initialAdminId',
    );
    final password = TextEditingController();
    final passwordConfirmation = TextEditingController();
    var role = initialRole;
    var permissions = Set<String>.from(
      initialPermissions ?? _rolePermissions[initialRole] ?? const {'read'},
    );
    var active = initialActive;
    final result = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, setDialogState) => AlertDialog(
          title: Text(editing ? 'تعديل صلاحيات المشرف' : 'إضافة مشرف'),
          content: SizedBox(
            width: 440,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  TextField(
                    controller: name,
                    maxLength: 80,
                    enabled: !editing,
                    decoration: const InputDecoration(
                      labelText: 'اسم المشرف',
                      prefixIcon: Icon(Icons.badge_outlined),
                    ),
                  ),
                  if (!editing) ...[
                    const SizedBox(height: 8),
                    TextField(
                      controller: telegramId,
                      keyboardType: TextInputType.number,
                      decoration: const InputDecoration(
                        labelText: 'معرّف تيليغرام (اختياري)',
                        prefixIcon: Icon(Icons.tag),
                        helperText:
                            'للتعريف في سجل التدقيق فقط؛ لا يمنح صلاحية أوامر البوت.',
                      ),
                    ),
                  ],
                  const SizedBox(height: 8),
                  TextField(
                    controller: password,
                    obscureText: true,
                    autocorrect: false,
                    enableSuggestions: false,
                    maxLength: 128,
                    decoration: InputDecoration(
                      labelText: editing
                          ? 'كلمة مرور جديدة (اختياري)'
                          : 'كلمة مرور يختارها المالك',
                      helperText: '12 حرفًا على الأقل؛ لا تُخزَّن كنص ظاهر',
                      prefixIcon: const Icon(Icons.lock_outline),
                    ),
                  ),
                  TextField(
                    controller: passwordConfirmation,
                    obscureText: true,
                    autocorrect: false,
                    enableSuggestions: false,
                    maxLength: 128,
                    decoration: const InputDecoration(
                      labelText: 'تأكيد كلمة المرور',
                      prefixIcon: Icon(Icons.lock_reset_outlined),
                    ),
                  ),
                  const SizedBox(height: 12),
                  DropdownButtonFormField<String>(
                    initialValue: role,
                    decoration: const InputDecoration(
                      labelText: 'مستوى الصلاحية',
                      prefixIcon: Icon(Icons.admin_panel_settings_outlined),
                    ),
                    items: [
                      for (final entry in _roles.entries)
                        DropdownMenuItem(
                          value: entry.key,
                          child: Text(entry.value.$1),
                        ),
                    ],
                    onChanged: (value) {
                      if (value != null) {
                        setDialogState(() {
                          role = value;
                          permissions = Set<String>.from(
                            _rolePermissions[value] ?? const {'read'},
                          );
                        });
                      }
                    },
                  ),
                  const SizedBox(height: 8),
                  Align(
                    alignment: AlignmentDirectional.centerStart,
                    child: Text(
                      _roles[role]?.$2 ?? '',
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ),
                  const SizedBox(height: 8),
                  for (final entry in _permissionLabels.entries)
                    CheckboxListTile(
                      contentPadding: EdgeInsets.zero,
                      dense: true,
                      title: Text(entry.value),
                      value: permissions.contains(entry.key),
                      onChanged: role == 'owner'
                          ? null
                          : (selected) => setDialogState(() {
                              if (selected == true) {
                                permissions.add(entry.key);
                              } else if (entry.key != 'read') {
                                permissions.remove(entry.key);
                              }
                            }),
                    ),
                  if (editing) ...[
                    const SizedBox(height: 12),
                    SwitchListTile(
                      contentPadding: EdgeInsets.zero,
                      title: const Text('الحساب مفعّل'),
                      value: active,
                      onChanged: (value) =>
                          setDialogState(() => active = value),
                    ),
                  ],
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('إلغاء'),
            ),
            FilledButton(
              onPressed: () {
                final trimmedName = name.text.trim();
                final parsedId = int.tryParse(telegramId.text.trim());
                if (!editing && trimmedName.isEmpty) {
                  toast(context, 'أدخل اسم المشرف', error: true);
                  return;
                }
                if (!editing &&
                    telegramId.text.trim().isNotEmpty &&
                    (parsedId == null || parsedId <= 0)) {
                  toast(context, 'معرّف تيليغرام غير صالح', error: true);
                  return;
                }
                final selectedPassword = password.text;
                if ((!editing || selectedPassword.isNotEmpty) &&
                    selectedPassword.length < 12) {
                  toast(
                    context,
                    'كلمة المرور يجب ألا تقل عن 12 حرفًا',
                    error: true,
                  );
                  return;
                }
                if (selectedPassword != passwordConfirmation.text) {
                  toast(context, 'تأكيد كلمة المرور غير مطابق', error: true);
                  return;
                }
                Navigator.pop(context, {
                  'name': trimmedName,
                  'admin_id': parsedId,
                  'role': role,
                  'permissions': permissions.toList(),
                  'active': active,
                  if (selectedPassword.isNotEmpty) 'password': selectedPassword,
                });
              },
              child: Text(editing ? 'حفظ الحساب' : 'إنشاء حساب المشرف'),
            ),
          ],
        ),
      ),
    );
    return result;
  }

  Future<void> _edit(Map<String, dynamic> item) async {
    final result = await _adminForm(
      initialName: item['name']?.toString() ?? '',
      initialRole: item['role']?.toString() ?? 'viewer',
      initialPermissions: {
        for (final permission
            in item['permissions'] as List<dynamic>? ??
                _rolePermissions[item['role']?.toString()]?.toList() ??
                const ['read'])
          permission.toString(),
      },
      initialAdminId: (item['admin_id'] as num?)?.toInt(),
      editing: true,
      initialActive: item['active'] == true,
    );
    if (result == null || !mounted) return;
    setState(() => _busy = true);
    try {
      await widget.api.put(
        '/api/admin/admins/${Uri.encodeComponent(item['id'].toString())}',
        {
          'role': result['role'],
          'permissions': result['permissions'],
          'active': result['active'],
          if (result['password'] != null) 'password': result['password'],
        },
      );
      if (!mounted) return;
      toast(
        context,
        result['password'] == null
            ? 'تم تحديث صلاحيات المشرف'
            : 'تم تحديث الحساب وكلمة المرور؛ سُجل الخروج من الجلسات السابقة',
      );
      await _load();
    } catch (error) {
      if (mounted) toast(context, _friendlyError(error), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _rotateOwnerToken() async {
    final confirmed = await confirmDialog(
      context,
      'تدوير رمز المالك',
      'سيُبطل هذا الإجراء رموز المالك الحالية ورموز البيئة القديمة. '
          'سيظهر الرمز الجديد مرة واحدة فقط؛ احفظه قبل إغلاق النافذة.',
      okLabel: 'تدوير الرمز',
      danger: true,
    );
    if (!confirmed || !mounted) return;
    setState(() => _busy = true);
    try {
      final result = await widget.api.post('/api/admin/admin-token/rotate', {});
      if (!mounted) return;
      await _showIssuedToken(
        'رمز المالك الجديد',
        result['token']?.toString() ?? '',
        useInThisBrowser: true,
      );
      await _load();
    } catch (error) {
      if (mounted) toast(context, _friendlyError(error), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _showIssuedToken(
    String title,
    String token, {
    required bool useInThisBrowser,
  }) async {
    if (token.isEmpty) {
      toast(context, 'لم يُعِد الخادم رمز الدخول', error: true);
      return;
    }
    await showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (dialogContext) => AlertDialog(
        title: Text(title),
        content: SizedBox(
          width: 520,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'انسخ الرمز وسلّمه للمشرف عبر قناة آمنة. لن يمكن استعادته من اللوحة بعد إغلاق هذه النافذة.',
              ),
              const SizedBox(height: 14),
              SelectableText(
                token,
                style: const TextStyle(
                  fontFamily: 'monospace',
                  fontWeight: FontWeight.w700,
                ),
              ),
            ],
          ),
        ),
        actions: [
          TextButton.icon(
            onPressed: () async {
              try {
                await Clipboard.setData(ClipboardData(text: token));
                if (dialogContext.mounted) toast(dialogContext, 'نُسخ الرمز');
              } catch (_) {
                if (dialogContext.mounted) {
                  toast(dialogContext, 'تعذر النسخ؛ حدّد الرمز وانسخه يدويًا');
                }
              }
            },
            icon: const Icon(Icons.copy),
            label: const Text('نسخ'),
          ),
          if (useInThisBrowser && widget.onUseOwnerToken != null)
            FilledButton(
              onPressed: () => widget.onUseOwnerToken!(token),
              child: const Text('استخدامه في هذا المتصفح'),
            )
          else
            FilledButton(
              onPressed: () => Navigator.pop(dialogContext),
              child: const Text('حفظت الرمز'),
            ),
        ],
      ),
    );
  }

  String _friendlyError(Object error) {
    if (error is ApiException) {
      return switch (error.code) {
        'last_owner_cannot_be_removed' => 'يجب إبقاء مالك واحد فعّال على الأقل',
        'bad_admin_name' => 'اسم المشرف مطلوب وبحد أقصى 80 حرفاً',
        'bad_admin_role' => 'مستوى الصلاحية غير صالح',
        'bad_admin_id' => 'معرّف تيليغرام غير صالح',
        'bad_admin_password' => 'كلمة المرور مطلوبة وبطول من 12 إلى 128 حرفًا',
        'admin_name_exists' => 'اسم الحساب مستخدم بالفعل؛ اختر اسمًا مختلفًا',
        'admin_not_found' => 'تعذر العثور على حساب المشرف',
        _ => errMsg(error),
      };
    }
    return errMsg(error);
  }

  String _roleName(Object? role) => _roles[role?.toString()]?.$1 ?? 'غير معروف';

  String _adminSubtitle(Map<String, dynamic> item) {
    final telegramId = (item['admin_id'] as num?)?.toInt() ?? 0;
    return '${_roleName(item['role'])} · '
        '${item['active'] == true ? 'فعّال' : 'موقوف'}'
        '${telegramId > 0 ? ' · Telegram: $telegramId' : ''}';
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null) return ErrorView(_error!, _load);
    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        SectionCard(
          'إدارة الوصول',
          icon: Icons.admin_panel_settings_outlined,
          subtitle:
              'حسابات مستقلة وصلاحيات واضحة؛ كلمات المرور تُخزَّن بمشتق آمن.',
          trailing: IconButton(
            tooltip: 'تحديث',
            onPressed: _busy ? null : _load,
            icon: const Icon(Icons.refresh),
          ),
          child: Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              FilledButton.icon(
                onPressed: _busy ? null : _create,
                icon: const Icon(Icons.person_add_alt_1),
                label: const Text('إضافة مشرف'),
              ),
              OutlinedButton.icon(
                onPressed: _busy ? null : _rotateOwnerToken,
                icon: const Icon(Icons.key_outlined),
                label: const Text('تغيير رمز المالك'),
              ),
              if (_busy)
                const Padding(
                  padding: EdgeInsetsDirectional.only(start: 8),
                  child: SizedBox(
                    width: 22,
                    height: 22,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                ),
            ],
          ),
        ),
        const SizedBox(height: 14),
        if (!_bootstrapDisabled)
          Card(
            child: ListTile(
              leading: const Icon(Icons.info_outline),
              title: const Text('رمز البيئة ما زال مفعّلاً'),
              subtitle: const Text(
                'يُستخدم إعداد الخادم الحالي حتى تدوير رمز المالك. التدوير يُبطل الرموز القديمة.',
              ),
            ),
          ),
        SectionCard(
          'المشرفون',
          icon: Icons.groups_2_outlined,
          subtitle:
              'يمكن إيقاف أي حساب أو تغيير مستواه دون كشف كلمة مروره الحالية.',
          child: _items.isEmpty
              ? const Padding(
                  padding: EdgeInsets.all(20),
                  child: Center(child: Text('لا توجد حسابات إضافية مسجلة')),
                )
              : Column(
                  children: [
                    for (final item in _items) ...[
                      ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: CircleAvatar(
                          child: Icon(
                            item['active'] == true
                                ? Icons.person_outline
                                : Icons.person_off_outlined,
                          ),
                        ),
                        title: Text(item['name']?.toString() ?? 'مشرف'),
                        subtitle: Text(_adminSubtitle(item)),
                        trailing: IconButton(
                          tooltip: 'تعديل الصلاحيات',
                          onPressed: _busy ? null : () => _edit(item),
                          icon: const Icon(Icons.tune),
                        ),
                      ),
                      if (!identical(item, _items.last))
                        const Divider(height: 1),
                    ],
                  ],
                ),
        ),
      ],
    );
  }
}
