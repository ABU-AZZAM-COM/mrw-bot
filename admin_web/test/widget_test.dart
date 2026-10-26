import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:admin_web/api.dart';
import 'package:admin_web/pages/blocked_page.dart';
import 'package:admin_web/pages/shared_dialogs.dart';
import 'package:admin_web/pages/whats_new_page.dart';
import 'package:admin_web/widgets.dart';

class _FakeApi implements AdminApi {
  _FakeApi({this.shrinkOnSecondPage = false});

  final bool shrinkOnSecondPage;
  final calls = <(String, Map<String, dynamic>)>[];
  final pagesRequested = <String>[];
  bool _secondPageRequested = false;

  @override
  Future<Map<String, dynamic>> get(
    String path, [
    Map<String, String>? query,
  ]) async {
    if (path == '/api/admin/blocked-users') {
      final page = query?['page'] ?? '0';
      pagesRequested.add(page);
      if (page == '1' && shrinkOnSecondPage) {
        _secondPageRequested = true;
        return {'total': 25, 'items': []};
      }
      return {
        'total': !shrinkOnSecondPage
            ? 1
            : _secondPageRequested
            ? 25
            : 26,
        'items': [
          {
            'user_id': 42,
            'full_name': 'مستخدم تجريبي',
            'username': 'test_user',
            'frozen_until': '9999-12-31T00:00:00',
            'reason': 'مخالفة متكررة',
          },
        ],
      };
    }
    throw ApiException(404, 'not_found');
  }

  @override
  Stream<Map<String, dynamic>> watchEvents() => const Stream.empty();

  @override
  Future<Uint8List> getBytes(String path) async =>
      throw ApiException(404, 'photo_unavailable');

  @override
  Future<Map<String, dynamic>> post(
    String path,
    Map<String, dynamic> body,
  ) async {
    calls.add((path, body));
    return {'ok': true, 'sent': true};
  }

  @override
  Future<Map<String, dynamic>> upload(
    String path, {
    required Map<String, String> fields,
    String? filename,
    Uint8List? bytes,
  }) async => throw UnimplementedError();

  @override
  Future<Map<String, dynamic>> put(
    String path,
    Map<String, dynamic> body,
  ) async => throw UnimplementedError();

  @override
  Future<Map<String, dynamic>> del(
    String path,
    Map<String, dynamic> body,
  ) async => throw UnimplementedError();

  @override
  String photoUrl(num reportId, {Object? bust}) => '';

  @override
  @override
  Future<void> download(String path) async {}
}

void main() {
  testWidgets('سجل ما الجديد يعرض الإصدار الحالي وإصداراته', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: WhatsNewPage())),
    );

    expect(find.text('ما الجديد في لوحة الإدارة؟'), findsOneWidget);
    expect(find.textContaining('2.7.20261026.2'), findsWidgets);
    expect(find.textContaining('2.7.20261026.1'), findsWidgets);
    expect(find.text('سجل الإصدارات ومعرفة الجديد'), findsOneWidget);
    expect(find.text('الإصدار المثبت'), findsOneWidget);
    expect(find.text('ميزات جديدة'), findsWidgets);
  });

  testWidgets('مؤشر المستخدم يميز النشاط المرصود دون ادعاء الاتصال الحي', (
    WidgetTester tester,
  ) async {
    Future<void> pumpActivity(Duration age) async {
      final lastSeen = DateTime.now().subtract(age).toIso8601String();
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(body: UserActivityIndicator(lastSeen: lastSeen)),
        ),
      );
      await tester.pump();
    }

    Color indicatorColor() {
      final container = tester.widget<Container>(
        find.descendant(
          of: find.byType(UserActivityIndicator),
          matching: find.byType(Container),
        ),
      );
      return (container.decoration! as BoxDecoration).color!;
    }

    await pumpActivity(const Duration(seconds: 30));
    expect(indicatorColor(), Colors.green);
    expect(
      find.byWidgetPredicate(
        (widget) =>
            widget is Tooltip &&
            (widget.message?.contains('ليس حالة اتصال Telegram فعلية') ??
                false),
      ),
      findsOneWidget,
    );

    await pumpActivity(const Duration(minutes: 10));
    expect(indicatorColor(), Colors.amber);

    await pumpActivity(const Duration(days: 2));
    expect(indicatorColor(), Colors.red);
  });

  testWidgets('رفض البلاغ يتطلب سببًا غير فارغًا', (WidgetTester tester) async {
    late BuildContext context;
    String? reason;
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (ctx) {
            context = ctx;
            return Scaffold(
              body: TextButton(
                onPressed: () async {
                  reason = await requiredTextDialog(
                    context,
                    title: 'سبب الرفض — إلزامي',
                    hint: 'اكتب السبب',
                  );
                },
                child: const Text('افتح'),
              ),
            );
          },
        ),
      ),
    );

    await tester.tap(find.text('افتح'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('تأكيد'));
    await tester.pumpAndSettle();
    expect(find.text('سبب الرفض — إلزامي'), findsOneWidget);

    await tester.enterText(find.byType(TextField), 'سبب موثق');
    await tester.tap(find.text('تأكيد'));
    await tester.pumpAndSettle();
    expect(reason, 'سبب موثق');
  });

  testWidgets('صفحة المحجوبين تعرض السبب وترسل رسالة فعلية عبر API', (
    WidgetTester tester,
  ) async {
    tester.view.physicalSize = const Size(1200, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final api = _FakeApi();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: BlockedPage(api: api)),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.textContaining('مخالفة متكررة'), findsOneWidget);
    await tester.tap(find.byTooltip('إرسال رسالة عبر البوت'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), 'رسالة اختبارية');
    await tester.tap(find.text('تأكيد'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('إرسال'));
    await tester.pumpAndSettle();

    expect(api.calls, hasLength(1));
    expect(api.calls.single.$1, '/api/admin/users/42/message');
    expect(api.calls.single.$2, {'text': 'رسالة اختبارية'});
    expect(find.text('أُرسلت الرسالة للمستخدم 42'), findsOneWidget);
  });

  testWidgets('تعيد صفحة المحجوبين إلى آخر صفحة متاحة بعد تغيّر النتائج', (
    WidgetTester tester,
  ) async {
    final api = _FakeApi(shrinkOnSecondPage: true);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: BlockedPage(api: api)),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('الصفحة 1 من 2'), findsOneWidget);

    await tester.tap(find.byTooltip('الصفحة التالية'));
    await tester.pumpAndSettle();

    expect(api.pagesRequested, ['0', '1', '0']);
    expect(find.text('الصفحة 1 من 1'), findsOneWidget);
    expect(find.textContaining('مخالفة متكررة'), findsOneWidget);
  });
}
