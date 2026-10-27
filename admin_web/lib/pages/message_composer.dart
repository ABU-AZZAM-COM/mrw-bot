import 'dart:typed_data';

import 'package:flutter/material.dart';

class MessageComposer extends StatelessWidget {
  const MessageComposer({
    super.key,
    required this.controller,
    required this.sending,
    required this.mediaBytes,
    required this.filename,
    required this.mediaType,
    required this.width,
    required this.onSend,
    required this.onPickMedia,
    required this.onRemoveMedia,
  });

  final TextEditingController controller;
  final bool sending;
  final Uint8List? mediaBytes;
  final String? filename;
  final String mediaType;
  final double width;
  final VoidCallback onSend;
  final ValueChanged<String> onPickMedia;
  final VoidCallback onRemoveMedia;

  void _insertEmoji(String emoji) {
    final selection = controller.selection;
    final start = selection.isValid ? selection.start : controller.text.length;
    final end = selection.isValid ? selection.end : start;
    final text = controller.text.replaceRange(start, end, emoji);
    controller.value = TextEditingValue(
      text: text,
      selection: TextSelection.collapsed(offset: start + emoji.length),
    );
  }

  IconData _mediaIcon(String type) => switch (type) {
    'video' => Icons.videocam_outlined,
    'audio' => Icons.audiotrack_outlined,
    'voice' => Icons.mic_none_outlined,
    'document' => Icons.insert_drive_file_outlined,
    _ => Icons.attach_file_rounded,
  };

  String _mediaLabel(String type) => switch (type) {
    'photo' => 'صورة',
    'video' => 'فيديو',
    'audio' => 'ملف صوتي',
    'voice' => 'رسالة صوتية',
    'document' => 'ملف',
    _ => 'مرفق',
  };

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.all(8),
      decoration: BoxDecoration(
        color: colors.surfaceContainerLow,
        border: Border(top: BorderSide(color: colors.outlineVariant)),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (mediaBytes != null)
            Container(
              margin: const EdgeInsetsDirectional.only(bottom: 8),
              padding: const EdgeInsetsDirectional.fromSTEB(8, 6, 12, 6),
              decoration: BoxDecoration(
                color: colors.surfaceContainerHighest,
                borderRadius: BorderRadius.circular(14),
              ),
              child: Row(
                children: [
                  if (mediaType == 'photo')
                    ClipRRect(
                      borderRadius: BorderRadius.circular(8),
                      child: Image.memory(
                        mediaBytes!,
                        width: 48,
                        height: 48,
                        fit: BoxFit.cover,
                        errorBuilder: (_, _, _) => const SizedBox(
                          width: 48,
                          height: 48,
                          child: Icon(Icons.broken_image_outlined),
                        ),
                      ),
                    )
                  else
                    CircleAvatar(
                      radius: 24,
                      child: Icon(_mediaIcon(mediaType)),
                    ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          _mediaLabel(mediaType),
                          style: Theme.of(context).textTheme.labelSmall,
                        ),
                        Text(
                          filename ?? 'مرفق جاهز للإرسال',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ],
                    ),
                  ),
                  IconButton(
                    tooltip: 'إزالة المرفق',
                    onPressed: sending ? null : onRemoveMedia,
                    icon: const Icon(Icons.close),
                  ),
                ],
              ),
            ),
          Container(
            constraints: const BoxConstraints(minHeight: 52, maxHeight: 140),
            decoration: BoxDecoration(
              color: colors.surface,
              borderRadius: BorderRadius.circular(28),
              border: Border.all(color: colors.outlineVariant),
              boxShadow: [
                BoxShadow(
                  color: colors.shadow.withValues(alpha: .05),
                  blurRadius: 8,
                  offset: const Offset(0, 2),
                ),
              ],
            ),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                PopupMenuButton<String>(
                  tooltip: 'إرفاق صورة أو ملف',
                  enabled: !sending,
                  icon: const Icon(Icons.attach_file_rounded),
                  onSelected: onPickMedia,
                  itemBuilder: (context) => const [
                    PopupMenuItem(
                      value: 'photo',
                      child: ListTile(
                        dense: true,
                        leading: Icon(Icons.photo_outlined),
                        title: Text('صورة'),
                      ),
                    ),
                    PopupMenuItem(
                      value: 'video',
                      child: ListTile(
                        dense: true,
                        leading: Icon(Icons.videocam_outlined),
                        title: Text('فيديو MP4'),
                      ),
                    ),
                    PopupMenuItem(
                      value: 'audio',
                      child: ListTile(
                        dense: true,
                        leading: Icon(Icons.audiotrack_outlined),
                        title: Text('ملف صوتي'),
                      ),
                    ),
                    PopupMenuItem(
                      value: 'voice',
                      child: ListTile(
                        dense: true,
                        leading: Icon(Icons.mic_none_outlined),
                        title: Text('رسالة صوتية محفوظة'),
                      ),
                    ),
                    PopupMenuItem(
                      value: 'document',
                      child: ListTile(
                        dense: true,
                        leading: Icon(Icons.insert_drive_file_outlined),
                        title: Text('ملف'),
                      ),
                    ),
                  ],
                ),
                PopupMenuButton<String>(
                  tooltip: 'إضافة رمز تعبيري',
                  enabled: !sending,
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
                        child: Text(
                          emoji,
                          style: const TextStyle(fontSize: 24),
                        ),
                      ),
                  ],
                ),
                Expanded(
                  child: TextField(
                    controller: controller,
                    enabled: !sending,
                    maxLength: 1000,
                    minLines: 1,
                    maxLines: 4,
                    textInputAction: TextInputAction.send,
                    onSubmitted: (_) {
                      if (!sending) onSend();
                    },
                    decoration: const InputDecoration(
                      hintText: 'اكتب رسالة...',
                      counterText: '',
                      border: InputBorder.none,
                      enabledBorder: InputBorder.none,
                      focusedBorder: InputBorder.none,
                      disabledBorder: InputBorder.none,
                      isDense: true,
                      contentPadding: EdgeInsetsDirectional.only(
                        top: 15,
                        bottom: 13,
                      ),
                    ),
                  ),
                ),
                const SizedBox(width: 4),
                Padding(
                  padding: const EdgeInsets.all(4),
                  child: IconButton.filled(
                    onPressed: sending ? null : onSend,
                    tooltip: sending ? 'جارٍ الإرسال' : 'إرسال الرسالة',
                    style: IconButton.styleFrom(
                      minimumSize: const Size(44, 44),
                      shape: const CircleBorder(),
                    ),
                    icon: sending
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.send_rounded),
                  ),
                ),
              ],
            ),
          ),
          if (width < 500)
            Padding(
              padding: const EdgeInsetsDirectional.only(top: 5, start: 8),
              child: Align(
                alignment: AlignmentDirectional.centerStart,
                child: Text(
                  'يمكنك إرفاق صورة أو فيديو أو صوت أو ملف من زر المشبك',
                  style: Theme.of(context).textTheme.labelSmall,
                ),
              ),
            ),
        ],
      ),
    );
  }
}
