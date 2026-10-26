import 'dart:async';
import 'dart:html' as html;
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';

class PublicationsPage extends StatefulWidget {
  const PublicationsPage({super.key, required this.api});

  final AdminApi api;

  @override
  State<PublicationsPage> createState() => _PublicationsPageState();
}

class _PublicationsPageState extends State<PublicationsPage> {
  final TextEditingController _caption = TextEditingController();
  String _mediaType = 'photo';
  Uint8List? _mediaBytes;
  String? _filename;
  String? _previewFileId;
  Map<String, dynamic>? _job;
  int _recipients = 0;
  bool _countCapped = false;
  bool _loading = true;
  bool _busy = false;
  bool _previewSent = false;

  bool get _hasMedia => _mediaBytes != null;
  bool get _jobActive =>
      _job?['status'] == 'running' ||
      _job?['status'] == 'queued' ||
      _job?['status'] == 'cancel_requested';

  @override
  void initState() {
    super.initState();
    _loadSummary();
  }

  @override
  void dispose() {
    _caption.dispose();
    super.dispose();
  }

  Future<void> _loadSummary() async {
    try {
      final result = await widget.api.get('/api/admin/publications/summary');
      if (!mounted) return;
      setState(() {
        _recipients = (result['recipients'] as num?)?.toInt() ?? 0;
        _countCapped = result['count_capped'] == true;
        _loading = false;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() => _loading = false);
      toast(context, errMsg(error), error: true);
    }
  }

  Future<void> _pickFile() async {
    final accept = switch (_mediaType) {
      'photo' => 'image/jpeg,image/png',
      'video' => 'video/mp4',
      _ => '*/*',
    };
    final picker = html.FileUploadInputElement()
      ..accept = accept
      ..multiple = false;
    picker.click();
    await picker.onChange.first;
    final files = picker.files;
    if (files == null) return;
    if (files.isEmpty) return;
    final file = files.first;
    final maxBytes = _mediaType == 'photo'
        ? 10 * 1024 * 1024
        : 20 * 1024 * 1024;
    if (file.size > maxBytes) {
      if (mounted) {
        toast(
          context,
          _mediaType == 'photo'
              ? 'حجم الصورة يتجاوز 10 ميغابايت'
              : 'حجم الملف يتجاوز 20 ميغابايت',
          error: true,
        );
      }
      return;
    }
    final reader = html.FileReader();
    reader.readAsArrayBuffer(file);
    await reader.onLoad.first;
    final result = reader.result;
    if (result is! ByteBuffer) {
      if (mounted) toast(context, 'تعذرت قراءة الملف', error: true);
      return;
    }
    if (!mounted) return;
    setState(() {
      _mediaBytes = Uint8List.view(result);
      _filename = file.name;
      _previewFileId = null;
      _previewSent = false;
    });
  }

  void _clearMedia() {
    setState(() {
      _mediaBytes = null;
      _filename = null;
      _previewFileId = null;
      _previewSent = false;
    });
  }

  Future<void> _sendPreview() async {
    final caption = _caption.text.trim();
    if (caption.length > 900) {
      toast(context, 'النص المرافق يتجاوز 900 حرف', error: true);
      return;
    }
    if (caption.isEmpty && !_hasMedia) {
      toast(context, 'أضف نصاً أو اختر ملفاً للنشر', error: true);
      return;
    }
    if (_hasMedia && _mediaBytes!.isEmpty) {
      toast(context, 'الملف المختار فارغ', error: true);
      return;
    }
    setState(() => _busy = true);
    try {
      final result = await widget.api.upload(
        '/api/admin/publications/preview',
        fields: {'caption': caption, 'media_type': _hasMedia ? _mediaType : ''},
        filename: _filename,
        bytes: _mediaBytes,
      );
      if (!mounted) return;
      setState(() {
        _previewFileId = result['file_id']?.toString();
        _previewSent = true;
      });
      toast(context, 'أُرسلت المعاينة إلى حساب المشرف');
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _publish() async {
    if (!_previewSent || (_hasMedia && _previewFileId == null)) {
      toast(context, 'أرسل المعاينة أولاً قبل النشر', error: true);
      return;
    }
    if (_countCapped) {
      toast(context, 'تجاوز عدد المستخدمين سقف الإرسال الآمن', error: true);
      return;
    }
    final confirmed = await confirmDialog(
      context,
      'تأكيد نشر المحتوى',
      'سيصل المحتوى إلى $_recipients مستخدم. لا يمكن التراجع عن الرسائل التي وصلت بالفعل.',
      okLabel: 'نشر الآن',
      danger: true,
    );
    if (!confirmed || !mounted) return;
    setState(() => _busy = true);
    try {
      final result = await widget.api.post('/api/admin/publications/send', {
        'caption': _caption.text.trim(),
        'media_type': _hasMedia ? _mediaType : '',
        'file_id': _previewFileId ?? '',
      });
      if (!mounted) return;
      if (result['job_id'] is! int) {
        toast(context, 'لا يوجد مستخدمون مسجلون لإرسال المنشور');
        return;
      }
      setState(() => _job = Map<String, dynamic>.from(result));
      await _watchJob(result['job_id'] as int);
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _watchJob(int id) async {
    while (mounted) {
      await Future<void>.delayed(const Duration(seconds: 2));
      if (!mounted) return;
      try {
        final result = await widget.api.get('/api/admin/broadcast/$id');
        if (!mounted) return;
        setState(() => _job = result);
        if (!_jobActive) {
          final status = result['status']?.toString() ?? 'unknown';
          toast(
            context,
            'انتهى النشر ($status): وصل ${result['sent'] ?? 0}، '
            'وتعذّر ${result['failed'] ?? 0} من ${result['total'] ?? 0}',
            error: status != 'completed',
          );
          return;
        }
      } catch (error) {
        if (mounted) toast(context, errMsg(error), error: true);
        return;
      }
    }
  }

  Future<void> _cancelJob() async {
    final id = _job?['job_id'];
    if (id is! int) return;
    try {
      await widget.api.post('/api/admin/broadcast/$id/cancel', {});
      if (!mounted) return;
      setState(() {
        _job = {...?_job, 'status': 'cancel_requested'};
      });
      toast(context, 'طُلب إيقاف النشر؛ قد تُرسل دفعة صغيرة قبل التوقف');
    } catch (error) {
      if (mounted) toast(context, errMsg(error), error: true);
    }
  }

  @override
  Widget build(BuildContext context) => ListView(
    padding: const EdgeInsets.all(16),
    children: [
      SectionCard(
        'إنشاء منشور',
        icon: Icons.perm_media_outlined,
        trailing: IconButton(
          tooltip: 'تحديث عدد المستلمين',
          onPressed: _loading ? null : _loadSummary,
          icon: const Icon(Icons.refresh),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              _loading
                  ? 'جارٍ تحميل عدد المستلمين…'
                  : _countCapped
                  ? 'عدد المستلمين تجاوز الحد الآمن (100,000). أوقف النشر حتى المراجعة.'
                  : 'سيصل المنشور إلى $_recipients مستخدم مسجل.',
              style: TextStyle(
                color: _countCapped
                    ? Theme.of(context).colorScheme.error
                    : null,
              ),
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              initialValue: _mediaType,
              decoration: const InputDecoration(labelText: 'نوع المحتوى'),
              items: const [
                DropdownMenuItem(value: 'photo', child: Text('صورة')),
                DropdownMenuItem(value: 'video', child: Text('فيديو MP4')),
                DropdownMenuItem(value: 'document', child: Text('ملف')),
              ],
              onChanged: _busy
                  ? null
                  : (value) {
                      if (value == null) return;
                      setState(() {
                        _mediaType = value;
                        _mediaBytes = null;
                        _filename = null;
                        _previewFileId = null;
                        _previewSent = false;
                      });
                    },
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _caption,
              enabled: !_busy,
              maxLength: 900,
              maxLines: 4,
              decoration: const InputDecoration(
                labelText: 'النص أو الوصف (اختياري عند إرفاق ملف)',
                alignLabelWithHint: true,
              ),
              onChanged: (_) {
                if (_previewSent) {
                  setState(() {
                    _previewSent = false;
                    _previewFileId = null;
                  });
                }
              },
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                OutlinedButton.icon(
                  onPressed: _busy ? null : _pickFile,
                  icon: const Icon(Icons.attach_file),
                  label: Text(_filename ?? 'اختيار ملف'),
                ),
                if (_hasMedia)
                  IconButton(
                    tooltip: 'إزالة الملف',
                    onPressed: _busy ? null : _clearMedia,
                    icon: const Icon(Icons.close),
                  ),
                if (_hasMedia)
                  Text(
                    '${(_mediaBytes!.lengthInBytes / (1024 * 1024)).toStringAsFixed(2)} ميغابايت',
                  ),
              ],
            ),
            const SizedBox(height: 12),
            if (_hasMedia)
              ClipRRect(
                borderRadius: BorderRadius.circular(12),
                child: SizedBox(
                  height: 150,
                  width: double.infinity,
                  child: _mediaType == 'photo'
                      ? Image.memory(
                          _mediaBytes!,
                          fit: BoxFit.contain,
                          errorBuilder: (_, _, _) => const Center(
                            child: Icon(Icons.broken_image_outlined, size: 44),
                          ),
                        )
                      : ColoredBox(
                          color: Theme.of(
                            context,
                          ).colorScheme.surfaceContainerHighest,
                          child: Icon(
                            _mediaType == 'video'
                                ? Icons.video_file_outlined
                                : Icons.insert_drive_file_outlined,
                            size: 44,
                          ),
                        ),
                ),
              ),
            if (_hasMedia) const SizedBox(height: 8),
            if (_previewSent)
              const ListTile(
                contentPadding: EdgeInsets.zero,
                leading: Icon(Icons.check_circle, color: Colors.green),
                title: Text('وصلت المعاينة إلى حساب المشرف'),
                subtitle: Text('تحقق من المحتوى في تيليجرام قبل النشر.'),
              ),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                OutlinedButton.icon(
                  onPressed: _busy ? null : _sendPreview,
                  icon: const Icon(Icons.visibility_outlined),
                  label: const Text('إرسال معاينة'),
                ),
                FilledButton.icon(
                  onPressed:
                      _busy ||
                          _loading ||
                          _countCapped ||
                          _jobActive ||
                          !_previewSent
                      ? null
                      : _publish,
                  icon: const Icon(Icons.campaign_outlined),
                  label: const Text('نشر للمستخدمين'),
                ),
                if (_jobActive)
                  OutlinedButton.icon(
                    onPressed: _cancelJob,
                    icon: const Icon(Icons.stop_circle_outlined),
                    label: const Text('إيقاف النشر'),
                  ),
              ],
            ),
          ],
        ),
      ),
      if (_job != null) ...[
        const SizedBox(height: 12),
        SectionCard(
          'حالة النشر',
          icon: Icons.track_changes_outlined,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              LinearProgressIndicator(
                value: (_job!['total'] as num? ?? 0) == 0
                    ? null
                    : (((_job!['sent'] as num? ?? 0) +
                                  (_job!['failed'] as num? ?? 0)) /
                              (_job!['total'] as num))
                          .clamp(0.0, 1.0)
                          .toDouble(),
              ),
              const SizedBox(height: 8),
              Text(
                'الحالة: ${_job!['status']} — وصل ${_job!['sent'] ?? 0}، '
                'وتعذّر ${_job!['failed'] ?? 0} من ${_job!['total'] ?? 0}',
              ),
            ],
          ),
        ),
      ],
    ],
  );
}
