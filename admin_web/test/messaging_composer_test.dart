import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:admin_web/pages/message_composer.dart';

void main() {
  testWidgets('message composer has send, emoji and attachment controls', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final controller = TextEditingController();
    var sendCount = 0;
    final selectedMediaTypes = <String>[];
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: MessageComposer(
            controller: controller,
            sending: false,
            mediaBytes: null,
            filename: null,
            mediaType: 'photo',
            width: 366,
            onSend: () => sendCount++,
            onPickMedia: selectedMediaTypes.add,
            onRemoveMedia: () {},
          ),
        ),
      ),
    );

    expect(find.byTooltip('إرفاق صورة أو ملف'), findsOneWidget);
    expect(find.byTooltip('إضافة رمز تعبيري'), findsOneWidget);
    expect(find.byTooltip('إرسال الرسالة'), findsOneWidget);
    expect(find.text('اكتب رسالة...'), findsOneWidget);

    await tester.tap(find.byTooltip('إرفاق صورة أو ملف'));
    await tester.pumpAndSettle();
    expect(find.text('صورة'), findsOneWidget);
    expect(find.text('فيديو MP4'), findsOneWidget);
    await tester.tap(find.text('رسالة صوتية محفوظة'));
    await tester.pumpAndSettle();
    expect(selectedMediaTypes, ['voice']);

    await tester.tap(find.byIcon(Icons.emoji_emotions_outlined));
    await tester.pumpAndSettle();
    await tester.tap(find.text('❤️'));
    expect(controller.text, '❤️');

    await tester.enterText(find.byType(TextField), 'رسالة تجريبية');
    await tester.tap(find.byTooltip('إرسال الرسالة'));
    expect(sendCount, 1);
    expect(controller.text, 'رسالة تجريبية');

    await tester.pumpWidget(const SizedBox.shrink());
    controller.dispose();
  });
}
