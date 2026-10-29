import 'dart:async';
import 'dart:convert';
import 'dart:html' as html;
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';
import 'publication_composer.dart';

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
  bool get _cancelRequested => _job?['status'] == 'cancel_requested';

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
    reader.readAsDataUrl(file);
    await reader.onLoad.first;
    final result = reader.result;
    if (result is! String) {
      if (mounted) toast(context, 'تعذرت قراءة الملف', error: true);
      return;
    }
    final separator = result.indexOf(',');
    if (separator < 0) {
      if (mounted) toast(context, 'تعذر تحليل الملف المحدد', error: true);
      return;
    }
    late final Uint8List bytes;
    try {
      bytes = base64Decode(result.substring(separator + 1));
    } on FormatException {
      if (mounted) toast(context, 'تعذر تحليل الملف المحدد', error: true);
      return;
    }
    if (bytes.isEmpty) {
      if (mounted) {
        toast(context, 'الملف المحدد فارغ أو تعذرت قراءته', error: true);
      }
      return;
    }
    if (!mounted) return;
    setState(() {
      _mediaBytes = bytes;
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
    if (!_hasMedia && caption.length < 3) {
      toast(
        context,
        'يجب أن يتكون نص المنشور من 3 أحرف على الأقل',
        error: true,
      );
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
        child: PublicationComposer(
          captionController: _caption,
          mediaType: _mediaType,
          mediaBytes: _mediaBytes,
          filename: _filename,
          recipients: _recipients,
          loadingRecipients: _loading,
          countCapped: _countCapped,
          busy: _busy,
          previewSent: _previewSent,
          jobActive: _jobActive,
          cancelRequested: _cancelRequested,
          onCaptionChanged: (_) => setState(() {
            if (_previewSent) {
              _previewSent = false;
              _previewFileId = null;
            }
          }),
          onMediaTypeChanged: (value) => setState(() {
            _mediaType = value;
            _mediaBytes = null;
            _filename = null;
            _previewFileId = null;
            _previewSent = false;
          }),
          onPickFile: _pickFile,
          onClearMedia: _clearMedia,
          onSendPreview: _sendPreview,
          onPublish: _publish,
          onCancelJob: _cancelJob,
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
