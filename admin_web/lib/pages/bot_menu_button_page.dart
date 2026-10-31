import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';

/// زر القائمة الجانبي (☰) في تيليجرام: افتراضي، أوامر، أو تطبيق ويب.
class BotMenuButtonPage extends StatefulWidget {
  const BotMenuButtonPage({super.key, required this.api});

  final AdminApi api;

  @override
  State<BotMenuButtonPage> createState() => _BotMenuButtonPageState();
}

class _BotMenuButtonPageState extends State<BotMenuButtonPage> {
  final _text = TextEditingController();
  final _url = TextEditingController();
  String _type = 'default';
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

  @override
  void dispose() {
    _text.dispose();
    _url.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final data = await widget.api.get('/api/admin/bot-menu-button');
      if (!mounted) return;
      final items = data['items'] is Map
          ? (data['items'] as Map).cast<String, dynamic>()
          : const <String, dynamic>{};
      setState(() {
        _type = '${items['type'] ?? 'default'}';
        _text.text = '${items['text'] ?? 'فتح التطبيق'}';
        _url.text = '${items['url'] ?? ''}';
        _customized = data['customized'] == true;
        _botReady = data['bot_ready'] == true;
        _miniAppUrl = '${data['mini_app_url'] ?? ''}';
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

  Future<void> _save() async {
    if (_type == 'web_app') {
      final text = _text.text.trim();
      final url = _url.text.trim();
      if (text.isEmpty || text.length > 64) {
        toast(context, 'نص الزر مطلوب وبحد أقصى 64 حرفًا.', error: true);
        return;
      }
      if (!url.toLowerCase().startsWith('https://')) {
        toast(context, 'تطبيق الويب يتطلب رابط HTTPS.', error: true);
        return;
      }
    }
    setState(() => _saving = true);
    try {
      final data = await widget.api.put('/api/admin/bot-menu-button', {
        'items': {
          'type': _type,
          'text': _text.text.trim(),
          'url': _type == 'web_app' ? _url.text.trim() : '',
        },
      });
      if (!mounted) return;
      setState(() {
        _customized = data['customized'] == true;
        _saving = false;
      });
      toast(
        context,
        _botReady
            ? 'تم الحفظ وتطبيق زر القائمة على البوت.'
            : 'تم الحفظ — سيُطبَّق عندما يصبح البوت جاهزًا.',
      );
    } catch (exc) {
      if (!mounted) return;
      setState(() => _saving = false);
      toast(context, errMsg(exc), error: true);
    }
  }

  Future<void> _reset() async {
    final confirmed = await confirmDialog(
      context,
      'إعادة زر القائمة الافتراضي',
      'سيعود زر القائمة إلى السلوك الافتراضي في تيليجرام. هل تريد المتابعة؟',
      okLabel: 'إعادة الافتراضي',
    );
    if (!confirmed) return;
    setState(() => _saving = true);
    try {
      final data = await widget.api.del('/api/admin/bot-menu-button', const {});
      if (!mounted) return;
      setState(() {
        final items = data['items'] is Map
            ? (data['items'] as Map).cast<String, dynamic>()
            : const <String, dynamic>{};
        _type = '${items['type'] ?? 'default'}';
        _text.text = '${items['text'] ?? 'فتح التطبيق'}';
        _url.text = '${items['url'] ?? ''}';
        _customized = false;
        _saving = false;
      });
      toast(context, 'تمت إعادة زر القائمة الافتراضي.');
    } catch (exc) {
      if (!mounted) return;
      setState(() => _saving = false);
      toast(context, errMsg(exc), error: true);
    }
  }

  Widget _typeCard(
    BuildContext context, {
    required String value,
    required String title,
    required String description,
    required IconData icon,
  }) {
    final colors = Theme.of(context).colorScheme;
    final selected = _type == value;
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      color: selected ? colors.primaryContainer.withValues(alpha: .35) : null,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: BorderSide(
          color: selected ? colors.primary : colors.outlineVariant,
          width: selected ? 1.5 : 1,
        ),
      ),
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: () => setState(() => _type = value),
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Row(
            children: [
              Icon(icon, color: selected ? colors.primary : colors.outline),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title, style: const TextStyle(fontWeight: FontWeight.w700)),
                    const SizedBox(height: 2),
                    Text(description, style: Theme.of(context).textTheme.bodySmall),
                  ],
                ),
              ),
              Icon(
                selected ? Icons.radio_button_checked : Icons.radio_button_off,
                color: selected ? colors.primary : colors.outline,
              ),
            ],
          ),
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
    final colors = Theme.of(context).colorScheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _typeCard(
          context,
          value: 'default',
          title: 'الافتراضي',
          description: 'زر ☰ يفتح قائمة أوامر البوت كما يقرّرها تيليجرام.',
          icon: Icons.menu_rounded,
        ),
        _typeCard(
          context,
          value: 'commands',
          title: 'قائمة الأوامر',
          description: 'يفتح قائمة الأوامر المحدّدة في تبويب «أوامر البوت».',
          icon: Icons.list_alt_rounded,
        ),
        _typeCard(
          context,
          value: 'web_app',
          title: 'تطبيق ويب',
          description: 'يفتح تطبيق البلاغ مباشرة من زر القائمة.',
          icon: Icons.rocket_launch_outlined,
        ),
        if (_type == 'web_app') ...[
          const SizedBox(height: 10),
          TextField(
            controller: _text,
            maxLength: 64,
            decoration: const InputDecoration(
              labelText: 'نص زر القائمة',
              isDense: true,
              border: OutlineInputBorder(),
              counterText: '',
            ),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: _url,
            maxLength: 512,
            keyboardType: TextInputType.url,
            decoration: InputDecoration(
              labelText: 'رابط تطبيق الويب (HTTPS)',
              hintText: _miniAppUrl.isEmpty ? 'https://…' : _miniAppUrl,
              helperText: _miniAppUrl.isEmpty
                  ? 'اضبط رابط Mini App في تبويب الإعدادات'
                  : 'الرابط الحالي في الإعدادات: $_miniAppUrl',
              isDense: true,
              border: const OutlineInputBorder(),
              counterText: '',
            ),
          ),
        ],
        const SizedBox(height: 14),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            FilledButton.icon(
              onPressed: _saving ? null : _save,
              icon: const Icon(Icons.save_outlined),
              label: const Text('حفظ وتطبيق'),
            ),
            OutlinedButton.icon(
              onPressed: _saving ? null : _load,
              icon: const Icon(Icons.refresh_rounded),
              label: const Text('تحديث'),
            ),
            TextButton.icon(
              onPressed: _saving ? null : _reset,
              icon: const Icon(Icons.restart_alt),
              label: const Text('إعادة الافتراضي'),
            ),
          ],
        ),
        if (!_botReady)
          Padding(
            padding: const EdgeInsets.only(top: 10),
            child: Text(
              'البوت غير جاهز الآن — سيُطبَّق الإعداد عند الإقلاع التالي.',
              style: TextStyle(color: colors.error, fontSize: 12),
            ),
          ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) => ListView(
    padding: const EdgeInsets.all(16),
    children: [
      SectionCard(
        'زر القائمة الجانبي',
        icon: Icons.more_horiz_rounded,
        subtitle: _customized ? 'إعداد مخصص محفوظ' : 'الإعداد الافتراضي',
        child: _body(context),
      ),
    ],
  );
}
