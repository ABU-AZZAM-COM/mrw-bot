import 'dart:async';
import 'dart:convert';
import 'dart:html' as html;
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';

class BotProfilePage extends StatefulWidget {
  const BotProfilePage({super.key, required this.api});

  final AdminApi api;

  @override
  State<BotProfilePage> createState() => _BotProfilePageState();
}

class _BotProfilePageState extends State<BotProfilePage> {
  final _name = TextEditingController();
  final _shortDescription = TextEditingController();
  final _description = TextEditingController();
  final _welcome = TextEditingController();
  bool _loading = true;
  bool _saving = false;
  bool _canEdit = false;
  bool _botReady = false;
  String _username = '';
  String? _photoName;
  Uint8List? _photoBytes;
  bool _hasWelcomePhoto = false;
  bool _removeWelcomePhoto = false;
  Uint8List? _savedWelcomePhotoBytes;
  String? _welcomePhotoName;
  Uint8List? _welcomePhotoBytes;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _name.dispose();
    _shortDescription.dispose();
    _description.dispose();
    _welcome.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final results = await Future.wait([
        widget.api.get('/api/admin/bot-profile'),
        widget.api.get('/api/admin/session'),
      ]);
      final profile = (results[0]['items'] as Map).cast<String, dynamic>();
      final hasWelcomePhoto = profile['has_welcome_photo'] == true;
      if (!mounted) return;
      setState(() {
        _name.text = '${profile['name'] ?? ''}';
        _shortDescription.text = '${profile['short_description'] ?? ''}';
        _description.text = '${profile['description'] ?? ''}';
        _welcome.text = '${profile['welcome_text'] ?? ''}';
        _username = '${profile['username'] ?? ''}';
        _botReady = results[0]['bot_ready'] == true;
        _canEdit = results[1]['role'] == 'owner';
        _hasWelcomePhoto = hasWelcomePhoto;
        _savedWelcomePhotoBytes = null;
        _removeWelcomePhoto = false;
        _loading = false;
      });
      if (hasWelcomePhoto) {
        final bytes = await widget.api.getBytes(
          '/api/admin/bot-profile/welcome-photo',
        );
        if (mounted) setState(() => _savedWelcomePhotoBytes = bytes);
      }
    } catch (error) {
      if (!mounted) return;
      setState(() => _loading = false);
      toast(context, errMsg(error), error: true);
    }
  }

  Future<void> _pickPhoto() async {
    final picker = html.FileUploadInputElement()
      ..accept = 'image/jpeg,.jpg,.jpeg'
      ..multiple = false;
    picker.click();
    await picker.onChange.first;
    final files = picker.files;
    if (files == null || files.isEmpty) return;
    final file = files.first;
    if (file.size > 10 * 1024 * 1024) {
      if (mounted) {
        toast(context, 'يجب ألا يتجاوز حجم الصورة 10 ميغابايت', error: true);
      }
      return;
    }
    final reader = html.FileReader()..readAsDataUrl(file);
    await reader.onLoad.first;
    final result = reader.result;
    if (result is! String) {
      if (mounted) toast(context, 'تعذرت قراءة الصورة', error: true);
      return;
    }
    final separator = result.indexOf(',');
    if (separator < 0) {
      if (mounted) toast(context, 'تعذر تحليل الصورة المحددة', error: true);
      return;
    }
    late final Uint8List bytes;
    try {
      bytes = base64Decode(result.substring(separator + 1));
    } on FormatException {
      if (mounted) toast(context, 'تعذر تحليل الصورة المحددة', error: true);
      return;
    }
    if (bytes.isEmpty) {
      if (mounted) toast(context, 'الصورة المحددة فارغة', error: true);
      return;
    }
    if (!mounted) return;
    setState(() {
      _photoBytes = bytes;
      _photoName = file.name;
    });
  }

  Future<void> _pickWelcomePhoto() async {
    final picker = html.FileUploadInputElement()
      ..accept = 'image/jpeg,image/png,.jpg,.jpeg,.png'
      ..multiple = false;
    picker.click();
    await picker.onChange.first;
    final files = picker.files;
    if (files == null || files.isEmpty) return;
    final file = files.first;
    if (file.size > 10 * 1024 * 1024) {
      if (mounted) {
        toast(context, 'يجب ألا يتجاوز حجم الصورة 10 ميغابايت', error: true);
      }
      return;
    }
    final reader = html.FileReader()..readAsArrayBuffer(file);
    await reader.onLoad.first;
    final result = reader.result;
    if (result is! ByteBuffer) {
      if (mounted) toast(context, 'تعذرت قراءة الصورة', error: true);
      return;
    }
    final bytes = result.asUint8List();
    final isJpeg =
        bytes.length >= 3 &&
        bytes[0] == 0xff &&
        bytes[1] == 0xd8 &&
        bytes[2] == 0xff;
    final isPng =
        bytes.length >= 8 &&
        bytes[0] == 0x89 &&
        bytes[1] == 0x50 &&
        bytes[2] == 0x4e &&
        bytes[3] == 0x47 &&
        bytes[4] == 0x0d &&
        bytes[5] == 0x0a &&
        bytes[6] == 0x1a &&
        bytes[7] == 0x0a;
    if (!isJpeg && !isPng) {
      if (mounted) {
        toast(context, 'اختر صورة JPEG أو PNG صالحة', error: true);
      }
      return;
    }
    if (!mounted) return;
    setState(() {
      _welcomePhotoBytes = bytes;
      _welcomePhotoName = file.name;
      _removeWelcomePhoto = false;
    });
  }

  Future<void> _saveWelcomePhoto() async {
    if (_welcomePhotoBytes == null && !_removeWelcomePhoto) return;
    setState(() => _saving = true);
    try {
      if (_removeWelcomePhoto) {
        await widget.api.del('/api/admin/bot-profile/welcome-photo', {});
        if (!mounted) return;
        setState(() {
          _hasWelcomePhoto = false;
          _savedWelcomePhotoBytes = null;
          _removeWelcomePhoto = false;
        });
        toast(context, 'أُزيلت صورة الترحيب');
      } else {
        await widget.api.upload(
          '/api/admin/bot-profile/welcome-photo',
          fields: const {},
          filename: _welcomePhotoName,
          bytes: _welcomePhotoBytes,
        );
        if (!mounted) return;
        setState(() {
          _hasWelcomePhoto = true;
          _savedWelcomePhotoBytes = _welcomePhotoBytes;
          _welcomePhotoBytes = null;
          _welcomePhotoName = null;
        });
        toast(context, 'حُفظت صورة الترحيب');
      }
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _previewWelcome() async {
    final text = _welcome.text.trim();
    if (text.isEmpty) {
      toast(context, 'اكتب نص البداية أولاً', error: true);
      return;
    }
    await showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('معاينة رسالة البداية'),
        content: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 520),
          child: SelectableText(text),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('إغلاق'),
          ),
        ],
      ),
    );
  }

  Future<void> _save() async {
    final name = _name.text.trim();
    final shortDescription = _shortDescription.text.trim();
    final description = _description.text.trim();
    final welcome = _welcome.text.trim();
    if (name.isEmpty || name.length > 64) {
      toast(context, 'اسم البوت مطلوب وبحد أقصى 64 حرفاً', error: true);
      return;
    }
    if (shortDescription.length > 120 ||
        description.length > 512 ||
        welcome.isEmpty ||
        welcome.length > 3500) {
      toast(context, 'تحقق من أطوال النبذة والوصف ورسالة البداية', error: true);
      return;
    }
    final confirmed = await confirmDialog(
      context,
      'تأكيد تحديث هوية البوت',
      'سيتم تحديث الاسم والنبذة والوصف ورسالة البداية في البوت مباشرة.'
          '${_photoBytes == null ? '' : '\nوسيتم استبدال صورة الحساب بالصورة المحددة.'}',
      okLabel: 'حفظ التغييرات',
    );
    if (!confirmed || !mounted) return;
    setState(() => _saving = true);
    try {
      await widget.api.upload(
        '/api/admin/bot-profile',
        fields: {
          'name': name,
          'short_description': shortDescription,
          'description': description,
          'welcome_text': welcome,
        },
        filename: _photoName,
        bytes: _photoBytes,
      );
      if (!mounted) return;
      setState(() {
        _photoBytes = null;
        _photoName = null;
      });
      toast(context, 'حُفظت هوية البوت ورسالة البداية');
      await _load();
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) => ListView(
    padding: const EdgeInsets.all(16),
    children: [
      SectionCard(
        'هوية البوت وتجربة البداية',
        icon: Icons.smart_toy_outlined,
        trailing: IconButton(
          tooltip: 'تحديث المعلومات',
          onPressed: _loading || _saving ? null : _load,
          icon: const Icon(Icons.refresh),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              'تحكم في المعلومات العامة التي تظهر في تيليجرام ورسالة الترحيب التي يراها المستخدم عند بدء المحادثة.',
            ),
            const SizedBox(height: 10),
            Text(
              _loading
                  ? 'جارٍ تحميل بيانات البوت…'
                  : _botReady
                  ? 'حساب البوت متصل${_username.isEmpty ? '' : ' — @$_username'}'
                  : 'البوت غير متصل حالياً؛ يمكنك مراجعة النصوص لكن الحفظ يتطلب اتصال البوت.',
              style: TextStyle(
                color: _botReady
                    ? Theme.of(context).colorScheme.primary
                    : Theme.of(context).colorScheme.error,
              ),
            ),
            if (!_canEdit && !_loading) ...[
              const SizedBox(height: 8),
              const Text('التعديل متاح لمالك اللوحة فقط.'),
            ],
          ],
        ),
      ),
      const SizedBox(height: 12),
      SectionCard(
        'معلومات الحساب في تيليجرام',
        icon: Icons.badge_outlined,
        child: Column(
          children: [
            TextField(
              controller: _name,
              enabled: _canEdit && !_loading && !_saving,
              maxLength: 64,
              decoration: const InputDecoration(
                labelText: 'اسم البوت',
                helperText: 'الاسم الظاهر أعلى صفحة الحساب.',
              ),
            ),
            TextField(
              controller: _shortDescription,
              enabled: _canEdit && !_loading && !_saving,
              maxLength: 120,
              maxLines: 2,
              decoration: const InputDecoration(
                labelText: 'النبذة القصيرة',
                helperText: 'تظهر بجانب معلومات البوت.',
              ),
            ),
            TextField(
              controller: _description,
              enabled: _canEdit && !_loading && !_saving,
              maxLength: 512,
              maxLines: 4,
              decoration: const InputDecoration(
                labelText: 'الوصف الكامل',
                helperText: 'اشرح الخدمات التي يقدمها البوت وما لا يقدمه.',
                alignLabelWithHint: true,
              ),
            ),
            const SizedBox(height: 8),
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: Wrap(
                spacing: 8,
                runSpacing: 8,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  OutlinedButton.icon(
                    onPressed: _canEdit && !_saving ? _pickPhoto : null,
                    icon: const Icon(Icons.add_a_photo_outlined),
                    label: Text(_photoName ?? 'اختيار صورة الحساب'),
                  ),
                  if (_photoBytes != null)
                    IconButton(
                      tooltip: 'إزالة الصورة المحددة',
                      onPressed: _saving
                          ? null
                          : () => setState(() {
                              _photoBytes = null;
                              _photoName = null;
                            }),
                      icon: const Icon(Icons.close),
                    ),
                  const Text('JPEG فقط — حتى 10 ميغابايت'),
                ],
              ),
            ),
            if (_photoBytes != null) ...[
              const SizedBox(height: 12),
              CircleAvatar(
                radius: 48,
                backgroundImage: MemoryImage(_photoBytes!),
              ),
            ],
          ],
        ),
      ),
      const SizedBox(height: 12),
      SectionCard(
        'صورة الترحيب بعد الضغط على بدء',
        icon: Icons.waving_hand_outlined,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              'تُرسل هذه الصورة داخل المحادثة بعد ضغط المستخدم على «بدء»، قبل نص الترحيب. لا تغيّر صورة الوصف التي تظهر قبل بدء المحادثة في BotFather.',
            ),
            const SizedBox(height: 12),
            if (_welcomePhotoBytes != null || _savedWelcomePhotoBytes != null)
              Center(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(
                    maxWidth: 480,
                    maxHeight: 280,
                  ),
                  child: Image.memory(
                    _welcomePhotoBytes ?? _savedWelcomePhotoBytes!,
                    fit: BoxFit.contain,
                  ),
                ),
              )
            else
              const Text('لا توجد صورة ترحيب محفوظة.'),
            const SizedBox(height: 10),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                OutlinedButton.icon(
                  onPressed: _canEdit && !_loading && !_saving
                      ? _pickWelcomePhoto
                      : null,
                  icon: const Icon(Icons.add_photo_alternate_outlined),
                  label: Text(_welcomePhotoName ?? 'اختيار صورة ترحيب'),
                ),
                if (_welcomePhotoBytes != null)
                  IconButton(
                    tooltip: 'إلغاء الصورة المحددة',
                    onPressed: _saving
                        ? null
                        : () => setState(() {
                            _welcomePhotoBytes = null;
                            _welcomePhotoName = null;
                          }),
                    icon: const Icon(Icons.close),
                  ),
                if (_hasWelcomePhoto)
                  TextButton.icon(
                    onPressed: !_canEdit || _loading || _saving
                        ? null
                        : () => setState(() {
                            _removeWelcomePhoto = !_removeWelcomePhoto;
                            if (_removeWelcomePhoto) {
                              _welcomePhotoBytes = null;
                              _welcomePhotoName = null;
                            }
                          }),
                    icon: Icon(
                      _removeWelcomePhoto
                          ? Icons.undo_outlined
                          : Icons.delete_outline,
                    ),
                    label: Text(
                      _removeWelcomePhoto ? 'إلغاء الإزالة' : 'إزالة الصورة',
                    ),
                  ),
                Text(
                  _removeWelcomePhoto
                      ? 'سيتم حذف الصورة عند الحفظ.'
                      : 'JPEG أو PNG — حتى 10 ميغابايت.',
                ),
              ],
            ),
            if (_canEdit && (_welcomePhotoBytes != null || _removeWelcomePhoto))
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: FilledButton.icon(
                  onPressed: _saving ? null : _saveWelcomePhoto,
                  icon: const Icon(Icons.save_outlined),
                  label: Text(
                    _removeWelcomePhoto
                        ? 'حفظ إزالة الصورة'
                        : 'حفظ صورة الترحيب',
                  ),
                ),
              ),
          ],
        ),
      ),
      const SizedBox(height: 12),
      SectionCard(
        'رسالة البداية',
        icon: Icons.waving_hand_outlined,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            TextField(
              controller: _welcome,
              enabled: _canEdit && !_loading && !_saving,
              maxLength: 3500,
              maxLines: 8,
              decoration: const InputDecoration(
                labelText: 'النص الذي يظهر عند الضغط على بدء',
                helperText:
                    'نص عادي مع أسطر جديدة. أزرار الخدمات الحالية تبقى كما هي.',
                alignLabelWithHint: true,
              ),
            ),
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: OutlinedButton.icon(
                onPressed: _loading ? null : _previewWelcome,
                icon: const Icon(Icons.visibility_outlined),
                label: const Text('معاينة النص'),
              ),
            ),
          ],
        ),
      ),
      const SizedBox(height: 12),
      SectionCard(
        'النشر والتدقيق',
        icon: Icons.verified_user_outlined,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              'التغييرات تُرسل إلى تيليجرام وتحفظ في سجل التدقيق. رسالة البداية نص عادي لتجنب تعطلها بسبب تنسيق غير صالح.',
            ),
            const SizedBox(height: 12),
            FilledButton.icon(
              onPressed: !_canEdit || _loading || _saving || !_botReady
                  ? null
                  : _save,
              icon: _saving
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.save_outlined),
              label: const Text('حفظ ونشر التغييرات'),
            ),
          ],
        ),
      ),
    ],
  );
}
