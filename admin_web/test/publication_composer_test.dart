import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:admin_web/pages/publication_composer.dart';

void main() {
  testWidgets('publication composer previews edits on a mobile viewport', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final controller = TextEditingController();
    var mediaType = 'photo';
    var previewCount = 0;
    var publishCount = 0;
    await tester.pumpWidget(
      MaterialApp(
        home: StatefulBuilder(
          builder: (context, setState) => Scaffold(
            body: ListView(
              padding: const EdgeInsets.all(12),
              children: [
                PublicationComposer(
                  captionController: controller,
                  mediaType: mediaType,
                  mediaBytes: null,
                  filename: null,
                  recipients: 42,
                  loadingRecipients: false,
                  countCapped: false,
                  busy: false,
                  previewSent: true,
                  jobActive: false,
                  cancelRequested: false,
                  onCaptionChanged: (_) => setState(() {}),
                  onMediaTypeChanged: (value) =>
                      setState(() => mediaType = value),
                  onPickFile: () {},
                  onClearMedia: () {},
                  onSendPreview: () => previewCount++,
                  onPublish: () => publishCount++,
                  onCancelJob: () {},
                ),
              ],
            ),
          ),
        ),
      ),
    );

    expect(find.text('سيصل المنشور إلى 42 مستخدم مسجل.'), findsOneWidget);
    expect(find.text('معاينة المستلم'), findsOneWidget);
    expect(find.textContaining('تعميم من الجهة المختصة'), findsOneWidget);
    await tester.enterText(find.byType(TextField), 'إعلان مهم للمستخدمين');
    await tester.pump();
    expect(
      find.byWidgetPredicate(
        (widget) =>
            widget is SelectableText && widget.data == 'إعلان مهم للمستخدمين',
      ),
      findsOneWidget,
    );

    await tester.tap(find.byTooltip('إضافة رمز تعبيري'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('❤️'));
    await tester.pump();
    expect(controller.text, contains('❤️'));

    await tester.tap(find.text('فيديو'));
    await tester.pumpAndSettle();
    expect(mediaType, 'video');

    await tester.tap(find.text('إرسال معاينة'));
    await tester.tap(find.text('نشر للمستخدمين'));
    expect(previewCount, 1);
    expect(publishCount, 1);
    expect(tester.takeException(), isNull);

    await tester.pumpWidget(const SizedBox.shrink());
    controller.dispose();
  });

  testWidgets('media preview shows its image and safe recipient warning', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1200, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final controller = TextEditingController(text: 'وصف الصورة');
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: PublicationComposer(
            captionController: controller,
            mediaType: 'photo',
            mediaBytes: Uint8List.fromList([1, 2, 3]),
            filename: 'photo.png',
            recipients: 100000,
            loadingRecipients: false,
            countCapped: true,
            busy: false,
            previewSent: false,
            jobActive: false,
            cancelRequested: false,
            onCaptionChanged: (_) {},
            onMediaTypeChanged: (_) {},
            onPickFile: () {},
            onClearMedia: () {},
            onSendPreview: () {},
            onPublish: () {},
            onCancelJob: () {},
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(
      find.textContaining('تجاوز عدد المستخدمين سقف الإرسال الآمن'),
      findsOneWidget,
    );
    expect(find.textContaining('منشور من الجهة المختصة'), findsOneWidget);
    expect(find.text('photo.png'), findsOneWidget);
    expect(find.byType(Image), findsOneWidget);
    expect(find.text('بدون وصف'), findsNothing);
    expect(tester.takeException(), isNull);

    await tester.pumpWidget(const SizedBox.shrink());
    controller.dispose();
  });

  testWidgets('active broadcast can be cancelled while the composer is busy', (
    tester,
  ) async {
    var cancelCount = 0;
    final controller = TextEditingController();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ListView(
            children: [
              PublicationComposer(
                captionController: controller,
                mediaType: 'photo',
                mediaBytes: null,
                filename: null,
                recipients: 42,
                loadingRecipients: false,
                countCapped: false,
                busy: true,
                previewSent: true,
                jobActive: true,
                cancelRequested: false,
                onCaptionChanged: (_) {},
                onMediaTypeChanged: (_) {},
                onPickFile: () {},
                onClearMedia: () {},
                onSendPreview: () {},
                onPublish: () {},
                onCancelJob: () => cancelCount++,
              ),
            ],
          ),
        ),
      ),
    );

    await tester.ensureVisible(find.text('إيقاف النشر'));
    await tester.tap(find.text('إيقاف النشر'));
    expect(cancelCount, 1);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
    controller.dispose();
  });
}
