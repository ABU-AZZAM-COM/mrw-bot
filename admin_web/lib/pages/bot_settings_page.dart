import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';

class _AnalyticsRow {
  _AnalyticsRow({
    required this.action,
    required this.label,
    required this.presses,
    required this.updatedAt,
  });

  final String action;
  final String label;
  final int presses;
  final String updatedAt;
}

/// إعدادات البوت العامة + تحليلات الأزرار + الروابط العميقة + حدود تيليجرام.
class BotSettingsPage extends StatefulWidget {
  const BotSettingsPage({super.key, required this.api});

  final AdminApi api;

  @override
  State<BotSettingsPage> createState() => _BotSettingsPageState();
}

class _BotSettingsPageState extends State<BotSettingsPage> {
  static const _rightsLabels = <String, String>{
    'is_anonymous': 'إخفاء هوية المشرف',
    'can_manage_chat': 'إدارة المحادثة',
    'can_delete_messages': 'حذف الرسائل',
    'can_manage_video_chats': 'إدارة محادثات الفيديو',
    'can_restrict_members': 'تقييد الأعضاء',
    'can_promote_members': 'ترقية أعضاء',
    'can_change_info': 'تغيير معلومات المجموعة',
    'can_invite_users': 'دعوة مستخدمين',
    'can_pin_messages': 'تثبيت الرسائل',
    'can_manage_topics': 'إدارة المواضيع',
  };

  final _miniAppUrl = TextEditingController();
  final _payload = TextEditingController();
  Map<String, bool> _rights = {};
  List<String> _rightsKeys = const [];
  List<_AnalyticsRow> _analytics = const [];
  Map<String, String> _links = const {};
  Map<String, dynamic> _botInfo = const {};
  int _total = 0;
  bool _loading = true;
  bool _saving = false;
  bool _applying = false;
  bool _customized = false;
  bool _botReady = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _miniAppUrl.dispose();
    _payload.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final results = await Future.wait([
        widget.api.get('/api/admin/bot-settings'),
        widget.api.get('/api/admin/bot-analytics'),
      ]);
      if (!mounted) return;
      final data = results[0];
      final items = data['items'] is Map
          ? (data['items'] as Map).cast<String, dynamic>()
          : const <String, dynamic>{};
      final rightsRaw = items['default_admin_rights'];
      final keysRaw = data['rights_keys'];
      final keys = <String>[];
      if (keysRaw is List) {
        for (final key in keysRaw) {
          keys.add('$key');
        }
      } else if (rightsRaw is Map) {
        keys.addAll(rightsRaw.keys.map((key) => '$key'));
      }
      final rights = <String, bool>{};
      for (final key in keys) {
        rights[key] = rightsRaw is Map && rightsRaw[key] == true;
      }
      final bot = data['bot'] is Map
          ? (data['bot'] as Map).cast<String, dynamic>()
          : const <String, dynamic>{};
      final analytics = results[1];
      final rows = <_AnalyticsRow>[];
      final analyticsItems = analytics['items'];
      if (analyticsItems is List) {
        for (final item in analyticsItems) {
          if (item is! Map) continue;
          rows.add(
            _AnalyticsRow(
              action: '${item['action'] ?? ''}',
              label: '${item['label'] ?? item['action'] ?? ''}',
              presses: (item['presses'] as num?)?.toInt() ?? 0,
              updatedAt: '${item['updated_at'] ?? ''}',
            ),
          );
        }
      }
      setState(() {
        _miniAppUrl.text = '${items['mini_app_url'] ?? ''}';
        _rightsKeys = keys;
        _rights = rights;
        _customized = items['default_admin_rights_customized'] == true;
        _botReady = data['bot_ready'] == true;
        _botInfo = bot;
        _analytics = rows;
        _total = (analytics['total'] as num?)?.toInt() ?? 0;
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

  Future<void> _saveSettings({required bool customizedRights}) async {
    final url = _miniAppUrl.text.trim();
    if (url.isNotEmpty && !url.toLowerCase().startsWith('https://')) {
      toast(context, 'رابط Mini App يجب أن يبدأ بـ https://', error: true);
      return;
    }
    setState(() => _saving = true);
    try {
      final data = await widget.api.put('/api/admin/bot-settings', {
        'items': {
          'mini_app_url': url,
          'default_admin_rights': _rights,
          'default_admin_rights_customized': customizedRights,
        },
      });
      if (!mounted) return;
      final items = data['items'] is Map
          ? (data['items'] as Map).cast<String, dynamic>()
          : const <String, dynamic>{};
      setState(() {
        _customized = items['default_admin_rights_customized'] == true;
        _saving = false;
      });
      toast(context, 'تم حفظ إعدادات البوت وتطبيقها.');
    } catch (exc) {
      if (!mounted) return;
      setState(() => _saving = false);
      toast(context, errMsg(exc), error: true);
    }
  }

  Future<void> _resetAnalytics() async {
    final confirmed = await confirmDialog(
      context,
      'تصفير تحليلات الأزرار',
      'ستُحذف كل عدّادات الضغط نهائيًا. هل تريد المتابعة؟',
      okLabel: 'تصفير',
      danger: true,
    );
    if (!confirmed) return;
    try {
      await widget.api.del('/api/admin/bot-analytics', const {});
      if (!mounted) return;
      setState(() {
        _analytics = const [];
        _total = 0;
      });
      toast(context, 'تم تصفير التحليلات.');
    } catch (exc) {
      if (!mounted) return;
      toast(context, errMsg(exc), error: true);
    }
  }

  Future<void> _generateLinks() async {
    try {
      final data = await widget.api.get(
        '/api/admin/bot-links',
        {'payload': _payload.text.trim()},
      );
      if (!mounted) return;
      final links = data['links'];
      setState(() {
        _links = links is Map
            ? {
                for (final entry in links.entries)
                  '${entry.key}': '${entry.value}',
              }
            : const {};
      });
      toast(context, 'تم توليد الروابط.');
    } catch (exc) {
      if (!mounted) return;
      toast(context, errMsg(exc), error: true);
    }
  }

  Future<void> _applyAll() async {
    setState(() => _applying = true);
    try {
      final data = await widget.api.post('/api/admin/bot-apply', const {});
      if (!mounted) return;
      final result = data['result'];
      final ok = result is Map && result['ok'] == true;
      setState(() => _applying = false);
      toast(
        context,
        ok
            ? 'أُعيد تطبيق كل إعدادات البوت على تيليجرام.'
            : 'تمت المحاولة، لكن بعض العناصر رفضتها تيليجرام.',
        error: !ok,
      );
    } catch (exc) {
      if (!mounted) return;
      setState(() => _applying = false);
      toast(context, errMsg(exc), error: true);
    }
  }

  Widget _linksSection(BuildContext context) {
    final entries = _links.entries
        .where((entry) => entry.value.isNotEmpty)
        .toList();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Expanded(
              child: TextField(
                controller: _payload,
                maxLength: 64,
                decoration: const InputDecoration(
                  labelText: 'معامل start (اختياري)',
                  hintText: 'مثال: promo2026',
                  helperText: 'حروف إنجليزية وأرقام و _ و - فقط',
                  isDense: true,
                  border: OutlineInputBorder(),
                  counterText: '',
                ),
              ),
            ),
            const SizedBox(width: 8),
            FilledButton.tonalIcon(
              onPressed: _generateLinks,
              icon: const Icon(Icons.link_rounded),
              label: const Text('توليد'),
            ),
          ],
        ),
        if (entries.isNotEmpty) ...[
          const SizedBox(height: 10),
          for (final entry in entries)
            ListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.link, size: 18),
              title: SelectableText(
                entry.value,
                style: const TextStyle(fontSize: 12),
              ),
              subtitle: Text(
                switch (entry.key) {
                  'start' => 'رابط البدء العادي',
                  'startgroup' => 'إضافة البوت إلى مجموعة',
                  'startapp' => 'فتح التطبيق مباشرة',
                  'plain' => 'رابط البوت بدون معامل',
                  _ => entry.key,
                },
                style: const TextStyle(fontSize: 10),
              ),
            ),
        ],
      ],
    );
  }

  Widget _analyticsSection(BuildContext context) {
    if (_analytics.isEmpty) {
      return const EmptyView(
        'لا ضغطات مسجّلة بعد — تظهر هنا فور استخدام المستخدمين للأزرار.',
        icon: Icons.touch_app_outlined,
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          'إجمالي الضغطات: $_total — إجراءات متتبَّعة: ${_analytics.length}',
          style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 12),
        ),
        const SizedBox(height: 8),
        for (final row in _analytics)
          Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: Row(
              children: [
                Expanded(
                  child: Text(
                    row.label,
                    style: const TextStyle(fontSize: 12),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                const SizedBox(width: 8),
                SizedBox(
                  width: 120,
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(6),
                    child: LinearProgressIndicator(
                      minHeight: 8,
                      value: _total == 0 ? 0 : row.presses / _total,
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                SizedBox(
                  width: 44,
                  child: Text(
                    '${row.presses}',
                    textAlign: TextAlign.end,
                    style: const TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
              ],
            ),
          ),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: TextButton.icon(
            onPressed: _resetAnalytics,
            icon: const Icon(Icons.delete_outline, size: 18),
            label: const Text('تصفير العدّادات'),
          ),
        ),
      ],
    );
  }

  Widget _readonlySection(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final username = '${_botInfo['username'] ?? ''}';
    final supportsInline = _botInfo['supports_inline_queries'] == true;
    bool flag(String key) => _botInfo[key] == true;
    Widget row(String title, String value, {bool ok = false}) => Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(
        children: [
          Icon(
            ok ? Icons.check_circle_outline : Icons.info_outline,
            size: 16,
            color: ok ? Colors.green.shade700 : colors.outline,
          ),
          const SizedBox(width: 6),
          Expanded(child: Text(title, style: const TextStyle(fontSize: 12))),
          Text(
            value,
            style: TextStyle(
              fontSize: 11,
              fontWeight: FontWeight.w600,
              color: ok ? Colors.green.shade700 : colors.onSurfaceVariant,
            ),
          ),
        ],
      ),
    );
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        row(
          'البوت متصل بالخدمة',
          _botReady ? 'جاهز' : 'غير جاهز',
          ok: _botReady,
        ),
        row('اسم المستخدم', username.isEmpty ? 'غير معروف' : '@$username'),
        row(
          'الاستعلامات الإنلاين',
          supportsInline ? 'مفعّلة' : 'معطّلة',
          ok: supportsInline,
        ),
        row('الانضمام للمجموعات', flag('can_join_groups') ? 'مسموح' : 'ممنوع'),
        row(
          'قراءة كل رسائل المجموعات',
          flag('can_read_all_group_messages') ? 'مسموح' : 'ممنوع',
        ),
        const SizedBox(height: 8),
        Container(
          padding: const EdgeInsets.all(10),
          decoration: BoxDecoration(
            color: colors.surfaceContainerHighest.withValues(alpha: .5),
            borderRadius: BorderRadius.circular(10),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'إعدادات لا يمكن تغييرها من اللوحة (BotFather فقط)',
                style: TextStyle(fontWeight: FontWeight.w700, fontSize: 12),
              ),
              const SizedBox(height: 4),
              Text(
                'اسم المستخدم، التوكن، تفعيل الإنلاين، خصوصية المجموعات، نطاق '
                'تطبيق الويب، الدفع، ونقل/حذف البوت — تُدار من @BotFather. '
                'هذه القيم تُقرأ من تيليجرام وتُعرض هنا للمرجعية فقط.',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
          ),
        ),
      ],
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
        SectionCard(
          'رابط تطبيق الويب (Mini App)',
          icon: Icons.link_rounded,
          subtitle: 'يُستخدم في زر التطبيق ولوحة تطبيق الويب وزر القائمة',
          child: Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _miniAppUrl,
                  maxLength: 512,
                  keyboardType: TextInputType.url,
                  decoration: const InputDecoration(
                    labelText: 'الرابط',
                    hintText: 'https://example.com/app',
                    isDense: true,
                    border: OutlineInputBorder(),
                    counterText: '',
                  ),
                ),
              ),
              const SizedBox(width: 8),
              FilledButton.icon(
                onPressed: _saving
                    ? null
                    : () => _saveSettings(customizedRights: _customized),
                icon: const Icon(Icons.save_outlined),
                label: const Text('حفظ'),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        SectionCard(
          'صلاحيات المشرف الافتراضية',
          icon: Icons.verified_user_outlined,
          subtitle: _customized
              ? 'مخصصة — تُطبَّق على البوت الحالي'
              : 'غير مخصصة — تيليجرام يستخدم افتراضياته',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              SwitchListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                value: _customized,
                title: const Text('تفعيل الصلاحيات المخصصة'),
                subtitle: const Text(
                  'عند التعطيل لا تُرسل أي صلاحيات افتراضية إلى تيليجرام.',
                  style: TextStyle(fontSize: 11),
                ),
                onChanged: (value) => setState(() => _customized = value),
              ),
              const Divider(height: 10),
              for (final key in _rightsKeys)
                SwitchListTile(
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  value: _rights[key] ?? false,
                  title: Text(
                    _rightsLabels[key] ?? key,
                    style: const TextStyle(fontSize: 12),
                  ),
                  onChanged: _customized
                      ? (value) => setState(() => _rights[key] = value)
                      : null,
                ),
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: FilledButton.icon(
                  onPressed: _saving
                      ? null
                      : () => _saveSettings(customizedRights: _customized),
                  icon: const Icon(Icons.save_outlined, size: 18),
                  label: const Text('حفظ الصلاحيات'),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        SectionCard(
          'تحليلات ضغطات الأزرار',
          icon: Icons.insights_outlined,
          subtitle: 'عدّادات حقيقية من ضغطات المستخدمين على أزرار القائمة',
          trailing: IconButton(
            tooltip: 'تحديث',
            onPressed: _load,
            icon: const Icon(Icons.refresh_rounded),
          ),
          child: _analyticsSection(context),
        ),
        const SizedBox(height: 12),
        SectionCard(
          'الروابط العميقة',
          icon: Icons.share_outlined,
          subtitle: 'روابط start / startgroup / startapp',
          child: _linksSection(context),
        ),
        const SizedBox(height: 12),
        SectionCard(
          'حالة تيليجرام وحدود BotFather',
          icon: Icons.shield_outlined,
          subtitle: 'قراءة فقط — ما لا تسمح به واجهة Bot API',
          child: _readonlySection(context),
        ),
        const SizedBox(height: 12),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: OutlinedButton.icon(
            onPressed: _applying ? null : _applyAll,
            icon: _applying
                ? const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.sync_rounded),
            label: const Text('إعادة تطبيق كل الإعدادات على تيليجرام'),
          ),
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) => ListView(
    padding: const EdgeInsets.all(16),
    children: [_body(context)],
  );
}
