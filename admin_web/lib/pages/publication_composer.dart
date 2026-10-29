import 'dart:typed_data';

import 'package:flutter/material.dart';

class PublicationComposer extends StatelessWidget {
  const PublicationComposer({
    super.key,
    required this.captionController,
    required this.mediaType,
    required this.mediaBytes,
    required this.filename,
    required this.recipients,
    required this.loadingRecipients,
    required this.countCapped,
    required this.busy,
    required this.previewSent,
    required this.jobActive,
    required this.cancelRequested,
    required this.onCaptionChanged,
    required this.onMediaTypeChanged,
    required this.onPickFile,
    required this.onClearMedia,
    required this.onSendPreview,
    required this.onPublish,
    required this.onCancelJob,
  });

  final TextEditingController captionController;
  final String mediaType;
  final Uint8List? mediaBytes;
  final String? filename;
  final int recipients;
  final bool loadingRecipients;
  final bool countCapped;
  final bool busy;
  final bool previewSent;
  final bool jobActive;
  final bool cancelRequested;
  final ValueChanged<String> onCaptionChanged;
  final ValueChanged<String> onMediaTypeChanged;
  final VoidCallback onPickFile;
  final VoidCallback onClearMedia;
  final VoidCallback onSendPreview;
  final VoidCallback onPublish;
  final VoidCallback onCancelJob;

  bool get _hasMedia => mediaBytes != null;

  void _insertEmoji(String emoji) {
    final selection = captionController.selection;
    final start = selection.isValid
        ? selection.start
        : captionController.text.length;
    final end = selection.isValid ? selection.end : start;
    final text = captionController.text.replaceRange(start, end, emoji);
    captionController.value = TextEditingValue(
      text: text,
      selection: TextSelection.collapsed(offset: start + emoji.length),
    );
    onCaptionChanged(text);
  }

  Widget _recipientBanner(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final message = loadingRecipients
        ? 'جارٍ تحميل عدد المستلمين…'
        : countCapped
        ? 'تجاوز عدد المستخدمين سقف الإرسال الآمن (100,000). النشر متوقف حتى المراجعة.'
        : 'سيصل المنشور إلى $recipients مستخدم مسجل.';
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(
        color: countCapped
            ? colors.errorContainer
            : colors.primaryContainer.withValues(alpha: .45),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Row(
        children: [
          Icon(
            countCapped ? Icons.warning_amber_rounded : Icons.groups_2_outlined,
            color: countCapped ? colors.error : colors.primary,
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              message,
              style: TextStyle(
                color: countCapped ? colors.onErrorContainer : null,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _mediaSelector(BuildContext context) => Wrap(
    spacing: 8,
    runSpacing: 8,
    children: [
      for (final (value, label, icon) in const [
        ('photo', 'صورة', Icons.image_outlined),
        ('video', 'فيديو', Icons.videocam_outlined),
        ('document', 'ملف', Icons.attach_file_rounded),
      ])
        ChoiceChip(
          avatar: Icon(icon, size: 18),
          label: Text(label),
          selected: mediaType == value,
          onSelected: busy
              ? null
              : (_) {
                  if (mediaType != value) onMediaTypeChanged(value);
                },
        ),
    ],
  );

  Widget _attachmentRow(BuildContext context) {
    final theme = Theme.of(context);
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        OutlinedButton.icon(
          onPressed: busy ? null : onPickFile,
          icon: const Icon(Icons.attach_file_rounded),
          label: Text(
            filename ??
                'إرفاق ${mediaType == 'photo'
                    ? 'صورة'
                    : mediaType == 'video'
                    ? 'فيديو'
                    : 'ملف'}',
          ),
        ),
        if (_hasMedia) ...[
          Text(
            '${(mediaBytes!.lengthInBytes / (1024 * 1024)).toStringAsFixed(2)} ميغابايت',
            style: theme.textTheme.bodySmall,
          ),
          IconButton(
            tooltip: 'إزالة المرفق',
            onPressed: busy ? null : onClearMedia,
            icon: const Icon(Icons.close),
          ),
        ],
      ],
    );
  }

  Widget _captionField(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return TextField(
      controller: captionController,
      enabled: !busy,
      maxLength: 900,
      maxLines: 6,
      minLines: 4,
      decoration: InputDecoration(
        hintText: _hasMedia ? 'أضف وصفًا للوسائط…' : 'اكتب نص المنشور هنا…',
        helperText: 'حتى 900 حرف',
        counterText: '',
        alignLabelWithHint: true,
        filled: true,
        fillColor: colors.surface,
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(16)),
        suffixIcon: PopupMenuButton<String>(
          tooltip: 'إضافة رمز تعبيري',
          enabled: !busy,
          icon: const Icon(Icons.emoji_emotions_outlined),
          onSelected: _insertEmoji,
          itemBuilder: (context) => [
            for (final emoji in const [
              '😀',
              '🙂',
              '😂',
              '❤️',
              '👍',
              '🙏',
              '✅',
              '📌',
              '⚠️',
              '✨',
            ])
              PopupMenuItem(
                value: emoji,
                child: Text(emoji, style: const TextStyle(fontSize: 24)),
              ),
          ],
        ),
      ),
      onChanged: onCaptionChanged,
    );
  }

  Widget _previewCard(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final caption = captionController.text.trim();
    final heading = _hasMedia
        ? '📨 منشور من الجهة المختصة'
        : '📢 تعميم من الجهة المختصة';
    return Container(
      constraints: const BoxConstraints(minHeight: 280),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: colors.surfaceContainerLow,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: colors.outlineVariant),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              CircleAvatar(
                radius: 17,
                backgroundColor: colors.primaryContainer,
                child: Icon(
                  Icons.campaign_outlined,
                  size: 19,
                  color: colors.primary,
                ),
              ),
              const SizedBox(width: 9),
              Expanded(
                child: Text(
                  'معاينة المستلم',
                  style: Theme.of(
                    context,
                  ).textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w700),
                ),
              ),
              const Icon(Icons.remove_red_eye_outlined, size: 18),
            ],
          ),
          const SizedBox(height: 14),
          Align(
            alignment: AlignmentDirectional.centerEnd,
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: DecoratedBox(
                decoration: BoxDecoration(
                  color: colors.primaryContainer.withValues(alpha: .5),
                  borderRadius: const BorderRadiusDirectional.only(
                    topStart: Radius.circular(18),
                    topEnd: Radius.circular(18),
                    bottomStart: Radius.circular(18),
                  ),
                ),
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      if (_hasMedia) ...[
                        ClipRRect(
                          borderRadius: BorderRadius.circular(12),
                          child: SizedBox(
                            width: double.infinity,
                            height: 150,
                            child: mediaType == 'photo'
                                ? Image.memory(
                                    mediaBytes!,
                                    fit: BoxFit.cover,
                                    errorBuilder: (_, _, _) => ColoredBox(
                                      color: colors.surfaceContainerHighest,
                                      child: const Icon(
                                        Icons.broken_image_outlined,
                                        size: 42,
                                      ),
                                    ),
                                  )
                                : ColoredBox(
                                    color: colors.surfaceContainerHighest,
                                    child: Center(
                                      child: Icon(
                                        mediaType == 'video'
                                            ? Icons.video_file_outlined
                                            : Icons.insert_drive_file_outlined,
                                        size: 48,
                                      ),
                                    ),
                                  ),
                          ),
                        ),
                        const SizedBox(height: 10),
                      ] else if (caption.isEmpty) ...[
                        Container(
                          height: 116,
                          alignment: Alignment.center,
                          decoration: BoxDecoration(
                            color: colors.surfaceContainerHighest,
                            borderRadius: BorderRadius.circular(12),
                          ),
                          child: Icon(
                            Icons.chat_bubble_outline_rounded,
                            size: 42,
                            color: colors.onSurfaceVariant,
                          ),
                        ),
                        const SizedBox(height: 10),
                      ],
                      Text(
                        heading,
                        style: const TextStyle(fontWeight: FontWeight.w700),
                      ),
                      if (!_hasMedia) ...[
                        const SizedBox(height: 4),
                        Text(
                          '────────────────────',
                          style: TextStyle(color: colors.outline),
                        ),
                      ],
                      if (caption.isNotEmpty) ...[
                        const SizedBox(height: 6),
                        SelectableText(caption),
                      ] else if (_hasMedia)
                        Padding(
                          padding: const EdgeInsets.only(top: 6),
                          child: Text(
                            'بدون وصف',
                            style: TextStyle(color: colors.onSurfaceVariant),
                          ),
                        )
                      else
                        Padding(
                          padding: const EdgeInsets.only(top: 6),
                          child: Text(
                            'سيظهر نص المنشور هنا أثناء الكتابة.',
                            style: TextStyle(color: colors.onSurfaceVariant),
                          ),
                        ),
                      const SizedBox(height: 8),
                      Align(
                        alignment: AlignmentDirectional.centerEnd,
                        child: Text(
                          'الآن',
                          style: Theme.of(context).textTheme.labelSmall
                              ?.copyWith(color: colors.onSurfaceVariant),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
          const SizedBox(height: 16),
          Row(
            children: [
              Icon(
                previewSent ? Icons.verified_outlined : Icons.info_outline,
                size: 18,
                color: previewSent ? colors.primary : colors.onSurfaceVariant,
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  previewSent
                      ? 'أُرسلت نسخة اختبارية إلى حساب المشرف؛ راجعها قبل النشر.'
                      : 'المعاينة تقريبية. أرسل نسخة اختبارية إلى حساب المشرف للتحقق النهائي.',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _editor(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      Text(
        'نوع المحتوى',
        style: Theme.of(
          context,
        ).textTheme.labelLarge?.copyWith(fontWeight: FontWeight.w700),
      ),
      const SizedBox(height: 8),
      _mediaSelector(context),
      const SizedBox(height: 14),
      _attachmentRow(context),
      const SizedBox(height: 12),
      _captionField(context),
      const SizedBox(height: 14),
      Wrap(
        spacing: 8,
        runSpacing: 8,
        children: [
          OutlinedButton.icon(
            onPressed: busy ? null : onSendPreview,
            icon: busy
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.visibility_outlined),
            label: const Text('إرسال معاينة'),
          ),
          FilledButton.icon(
            onPressed:
                busy ||
                    loadingRecipients ||
                    countCapped ||
                    jobActive ||
                    !previewSent
                ? null
                : onPublish,
            icon: const Icon(Icons.campaign_outlined),
            label: const Text('نشر للمستخدمين'),
          ),
          if (jobActive)
            OutlinedButton.icon(
              onPressed: cancelRequested ? null : onCancelJob,
              icon: Icon(
                cancelRequested
                    ? Icons.hourglass_top_rounded
                    : Icons.stop_circle_outlined,
              ),
              label: Text(
                cancelRequested ? 'جارٍ إيقاف النشر…' : 'إيقاف النشر',
              ),
            ),
        ],
      ),
      if (previewSent) ...[
        const SizedBox(height: 8),
        const Text(
          'تغيير النص أو الملف يلغي تأكيد المعاينة، ويجب إرسالها مجددًا قبل النشر.',
          style: TextStyle(fontSize: 12),
        ),
      ],
    ],
  );

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      _recipientBanner(context),
      const SizedBox(height: 16),
      LayoutBuilder(
        builder: (context, constraints) {
          if (constraints.maxWidth >= 860) {
            return Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(flex: 6, child: _editor(context)),
                const SizedBox(width: 20),
                Expanded(flex: 5, child: _previewCard(context)),
              ],
            );
          }
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              _editor(context),
              const SizedBox(height: 18),
              _previewCard(context),
            ],
          );
        },
      ),
    ],
  );
}
