import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:admin_web/pages/bot_button_map_page.dart';

void main() {
  testWidgets('button map shows existing flow and edits only a local draft', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: BotButtonMapPage())),
    );
    await tester.pumpAndSettle();

    expect(find.text('مختبر تصميم — لا يغيّر البوت'), findsOneWidget);
    expect(find.text('📝 إرسال بلاغ جديد'), findsWidgets);
    expect(find.text('اختيار قسم البلاغ'), findsWidgets);
    expect(find.text('usr:report'), findsOneWidget);

    await tester.enterText(
      find.byKey(const ValueKey('button-label-menu_report-0')),
      '📝 إرسال بلاغ',
    );
    await tester.pump();
    expect(find.text('📝 إرسال بلاغ'), findsWidgets);

    final saveButton = find.text('حفظ مسودة تجريبية');
    await tester.ensureVisible(saveButton);
    await tester.tap(saveButton);
    await tester.pump();
    expect(
      find.text('حُفظت نسخة المسودة داخل هذه الجلسة فقط — لم يتغير البوت.'),
      findsOneWidget,
    );

    await tester.enterText(
      find.byKey(const ValueKey('button-label-menu_report-0')),
      'تغيير مؤقت',
    );
    await tester.pump();
    final restoreButton = find.text('استعادة المسودة');
    await tester.ensureVisible(restoreButton);
    await tester.tap(restoreButton);
    await tester.pump();
    expect(find.text('📝 إرسال بلاغ'), findsWidgets);
    expect(find.text('تغيير مؤقت'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('phone preview simulates menu to report categories locally', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: BotButtonMapPage())),
    );

    await tester.tap(find.text('معاينة الهاتف'));
    await tester.pumpAndSettle();
    expect(
      find.text('محاكاة محلية — لا يتم إرسال أي تفاعل إلى Telegram.'),
      findsOneWidget,
    );

    await tester.tap(find.byKey(const ValueKey('preview-button-menu_report')));
    await tester.pumpAndSettle();
    expect(find.text('اختيار قسم البلاغ'), findsWidgets);
    expect(find.text('🚨 تجاوزات وسلوك فردي'), findsOneWidget);
    expect(find.textContaining('لا يتم إرسال أي تفاعل'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
