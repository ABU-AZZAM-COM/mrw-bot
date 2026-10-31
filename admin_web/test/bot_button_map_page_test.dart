import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:admin_web/demo_api.dart';
import 'package:admin_web/pages/bot_button_map_page.dart';

void main() {
  testWidgets('bot buttons editor loads, saves live and resets to defaults', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(420, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: BotButtonMapPage(api: DemoApi())),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('تعديل حي — يُطبَّق على البوت فورًا'), findsOneWidget);
    expect(find.text('📝 تقديم بلاغ جديد'), findsWidgets);

    final firstLabel = find.byType(TextFormField).first;
    await tester.enterText(firstLabel, '📝 بلاغ مستعجل');
    await tester.pump();

    final saveButton = find.text('حفظ وتطبيق على البوت');
    await tester.ensureVisible(saveButton);
    await tester.tap(saveButton);
    await tester.pumpAndSettle();
    expect(
      find.text('تم الحفظ — الأزرار الجديدة ظاهرة للمستخدمين الآن.'),
      findsOneWidget,
    );
    expect(find.text('📝 بلاغ مستعجل'), findsWidgets);

    final previewButton = find.text('معاينة');
    await tester.ensureVisible(previewButton);
    await tester.tap(previewButton);
    await tester.pumpAndSettle();
    expect(find.text('معاينة قائمة البوت'), findsOneWidget);
    expect(find.textContaining('هكذا ستظهر الأزرار'), findsOneWidget);

    final resetButton = find.text('إعادة الافتراضي');
    await tester.ensureVisible(resetButton);
    await tester.tap(resetButton);
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'إعادة الافتراضي'));
    await tester.pumpAndSettle();
    expect(find.text('📝 تقديم بلاغ جديد'), findsWidgets);
    expect(tester.takeException(), isNull);
  });
}
