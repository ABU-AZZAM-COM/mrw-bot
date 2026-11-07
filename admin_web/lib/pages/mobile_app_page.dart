import 'package:flutter/material.dart';

import '../api.dart';
import '../design_tokens.dart';
import '../widgets.dart';

class MobileAppPage extends StatefulWidget {
  const MobileAppPage({super.key, required this.api});

  final AdminApi api;

  @override
  State<MobileAppPage> createState() => _MobileAppPageState();
}

class _MobileAppPageState extends State<MobileAppPage> {
  final _version = TextEditingController();
  final _apkUrl = TextEditingController();
  final _sha256 = TextEditingController();
  final _releaseNotes = TextEditingController();
  final _statusMessage = TextEditingController();
  String _status = 'active';
  String _updateMode = 'optional';
  bool _loading = true;
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _version.dispose();
    _apkUrl.dispose();
    _sha256.dispose();
    _releaseNotes.dispose();
    _statusMessage.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final response = await widget.api.get('/api/admin/mobile-app');
      final policy =
          (response['policy'] as Map?)?.cast<String, dynamic>() ??
          const <String, dynamic>{};
      if (!mounted) return;
      setState(() {
        _status = policy['status']?.toString() ?? 'active';
        _statusMessage.text = policy['status_message']?.toString() ?? '';
        _version.text = policy['latest_version']?.toString() ?? '';
        _updateMode = policy['update_mode']?.toString() ?? 'optional';
        _apkUrl.text = policy['apk_url']?.toString() ?? '';
        _sha256.text = policy['sha256']?.toString() ?? '';
        _releaseNotes.text = policy['release_notes']?.toString() ?? '';
      });
    } catch (error) {
      if (mounted) setState(() => _error = errMsg(error));
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  String? _validate() {
    final version = _version.text.trim();
    final urlText = _apkUrl.text.trim();
    final hash = _sha256.text.trim();
    if (version.isEmpty != urlText.isEmpty || urlText.isEmpty != hash.isEmpty) {
      return 'لإضافة إصدار، أدخل رقم الإصدار والرابط وبصمة SHA-256 معًا.';
    }
    if (version.isNotEmpty &&
        !RegExp(r'^\d+(?:\.\d+){1,3}$').hasMatch(version)) {
      return 'رقم الإصدار يجب أن يتكون من أرقام مفصولة بنقاط.';
    }
    final uri = Uri.tryParse(urlText);
    if (urlText.isNotEmpty &&
        (uri == null || uri.scheme != 'https' || uri.host.isEmpty)) {
      return 'رابط APK يجب أن يكون رابط HTTPS مباشرًا.';
    }
    if (hash.isNotEmpty && !RegExp(r'^[a-fA-F0-9]{64}$').hasMatch(hash)) {
      return 'أدخل بصمة SHA-256 كاملة مكوّنة من 64 رمزًا.';
    }
    if (_updateMode == 'required' && version.isEmpty) {
      return 'التحديث الإلزامي يحتاج إلى إصدار APK ورابطه وبصمته.';
    }
    if (_status != 'active' && _statusMessage.text.trim().isEmpty) {
      return 'اكتب الرسالة التي ستظهر عند إيقاف التطبيق.';
    }
    return null;
  }

  Future<void> _save() async {
    final validation = _validate();
    if (validation != null) {
      toast(context, validation, error: true);
      return;
    }
    if (_status == 'disabled') {
      final confirmed = await confirmDialog(
        context,
        'إيقاف تطبيق الهاتف نهائيًا',
        'سيُمنع التطبيق من استخدام واجهة الإدارة حتى تعيد تفعيله من لوحة الويب.',
        okLabel: 'إيقاف التطبيق',
        danger: true,
      );
      if (!confirmed || !mounted) return;
    }
    setState(() => _saving = true);
    try {
      await widget.api.put('/api/admin/mobile-app', {
        'status': _status,
        'status_message': _statusMessage.text.trim(),
        'latest_version': _version.text.trim(),
        'update_mode': _updateMode,
        'apk_url': _apkUrl.text.trim(),
        'sha256': _sha256.text.trim().toLowerCase(),
        'release_notes': _releaseNotes.text.trim(),
      });
      if (mounted) toast(context, 'حُفظت سياسة تطبيق الهاتف وتُطبّق فورًا.');
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text('تعذر تحميل إعدادات تطبيق الهاتف: $_error'),
            const SizedBox(height: AdminTokens.s3),
            OutlinedButton.icon(
              onPressed: _load,
              icon: const Icon(Icons.refresh),
              label: const Text('إعادة المحاولة'),
            ),
          ],
        ),
      );
    }
    final colors = Theme.of(context).colorScheme;
    return ListView(
      padding: const EdgeInsets.all(AdminTokens.pageInset),
      children: [
        ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 820),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                'تطبيق الهاتف',
                style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                  fontWeight: FontWeight.w800,
                ),
              ),
              const SizedBox(height: AdminTokens.s2),
              Text(
                'تحكم في إتاحة تطبيق Android وإصداراته. يظل التثبيت بموافقة '
                'المستخدم عبر مُثبّت Android الرسمي.',
                style: TextStyle(color: colors.onSurfaceVariant),
              ),
              const SizedBox(height: AdminTokens.s6),
              _sectionTitle(context, 'حالة التطبيق'),
              DropdownButtonFormField<String>(
                initialValue: _status,
                decoration: const InputDecoration(labelText: 'الإتاحة'),
                items: const [
                  DropdownMenuItem(
                    value: 'active',
                    child: Text('يعمل بشكل طبيعي'),
                  ),
                  DropdownMenuItem(value: 'paused', child: Text('إيقاف مؤقت')),
                  DropdownMenuItem(
                    value: 'disabled',
                    child: Text('إيقاف دائم'),
                  ),
                ],
                onChanged: _saving
                    ? null
                    : (value) => setState(() => _status = value ?? 'active'),
              ),
              if (_status != 'active') ...[
                const SizedBox(height: AdminTokens.s3),
                TextField(
                  controller: _statusMessage,
                  maxLength: 1000,
                  maxLines: 2,
                  decoration: const InputDecoration(
                    labelText: 'رسالة الإيقاف',
                    hintText: 'تظهر لمستخدمي التطبيق أثناء الإيقاف',
                  ),
                ),
              ],
              const SizedBox(height: AdminTokens.s6),
              const Divider(),
              const SizedBox(height: AdminTokens.s4),
              _sectionTitle(context, 'إصدار Android'),
              TextField(
                controller: _version,
                enabled: !_saving,
                decoration: const InputDecoration(
                  labelText: 'رقم الإصدار الجديد',
                  hintText: 'مثال: 2.8.20261107',
                  helperText: 'يجب أن يطابق versionName داخل ملف APK.',
                ),
              ),
              const SizedBox(height: AdminTokens.s3),
              TextField(
                controller: _apkUrl,
                enabled: !_saving,
                keyboardType: TextInputType.url,
                decoration: const InputDecoration(
                  labelText: 'رابط APK المباشر (HTTPS)',
                  hintText: 'رابط HTTPS عام ينزّل ملف APK مباشرة',
                ),
              ),
              const SizedBox(height: AdminTokens.s3),
              TextField(
                controller: _sha256,
                enabled: !_saving,
                maxLength: 64,
                decoration: const InputDecoration(
                  labelText: 'بصمة APK — SHA-256',
                ),
              ),
              const SizedBox(height: AdminTokens.s2),
              SegmentedButton<String>(
                segments: const [
                  ButtonSegment(
                    value: 'optional',
                    label: Text('اختياري'),
                    icon: Icon(Icons.schedule_outlined),
                  ),
                  ButtonSegment(
                    value: 'required',
                    label: Text('إلزامي'),
                    icon: Icon(Icons.system_update_alt),
                  ),
                ],
                selected: {_updateMode},
                onSelectionChanged: _saving
                    ? null
                    : (values) => setState(() => _updateMode = values.first),
              ),
              const SizedBox(height: AdminTokens.s3),
              TextField(
                controller: _releaseNotes,
                enabled: !_saving,
                maxLength: 4000,
                maxLines: 4,
                decoration: const InputDecoration(
                  labelText: 'ملاحظات الإصدار',
                  hintText: 'ما الذي تغيّر في هذا الإصدار؟',
                ),
              ),
              const SizedBox(height: AdminTokens.s4),
              Text(
                'التطبيق ينزّل الملف من الرابط داخل التطبيق، ويتحقق من '
                'البصمة قبل طلب التثبيت. يجب توقيع الإصدار الجديد بالمفتاح '
                'نفسه المستخدم في النسخة المثبتة.',
                style: Theme.of(
                  context,
                ).textTheme.bodySmall?.copyWith(color: colors.onSurfaceVariant),
              ),
              const SizedBox(height: AdminTokens.s5),
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: FilledButton.icon(
                  onPressed: _saving ? null : _save,
                  icon: _saving
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.save_outlined),
                  label: Text(_saving ? 'جارٍ الحفظ…' : 'حفظ سياسة التطبيق'),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _sectionTitle(BuildContext context, String title) => Padding(
    padding: const EdgeInsets.only(bottom: AdminTokens.s3),
    child: Text(
      title,
      style: Theme.of(
        context,
      ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
    ),
  );
}
