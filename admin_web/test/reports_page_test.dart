import 'dart:typed_data';

import 'package:admin_web/api.dart';
import 'package:admin_web/pages/reports_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class _ReportsApi implements AdminApi {
  final calls = <(String, Map<String, String>?)>[];

  @override
  Future<Map<String, dynamic>> get(
    String path, [
    Map<String, String>? query,
  ]) async {
    calls.add((path, query));
    if (path == '/api/admin/vtypes') {
      return {
        'items': ['سلوك فردي'],
      };
    }
    if (path == '/api/admin/meta') {
      return {
        'admin_ids': [11],
        'vtypes': ['سلوك فردي'],
      };
    }
    if (path == '/api/admin/reports/42') {
      return {
        'item': {
          'id': 42,
          'seq': 'MRV-2026-0042',
          'status': 'جديد',
          'full_name': 'مستخدم تجريبي',
          'user_id': 155,
          'username': 'test_user',
          'violation_type': 'سلوك فردي',
          'priority': 'urgent',
          'created_at': '2026-10-27T08:30:00',
          'user_last_at': '2026-10-27T08:29:00',
          'has_photo': false,
          'description': 'تفاصيل بلاغ تجريبي',
        },
      };
    }
    if (path == '/api/admin/reports') {
      return {
        'total': 1,
        'items': [
          {
            'id': 42,
            'seq': 'MRV-2026-0042',
            'status': 'جديد',
            'full_name': 'مستخدم تجريبي',
            'user_id': 155,
            'username': 'test_user',
            'violation_type': 'سلوك فردي',
            'priority': 'urgent',
            'created_at': '2026-10-27T08:30:00',
            'user_last_at': '2026-10-27T08:29:00',
            'has_photo': false,
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
      throw ApiException(404, 'not_found');

  @override
  Future<Map<String, dynamic>> post(
    String path,
    Map<String, dynamic> body,
  ) async => throw UnimplementedError();

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
  Future<void> download(String path) async => throw UnimplementedError();
}

void main() {
  for (final size in const [Size(390, 844), Size(1280, 900)]) {
    testWidgets('يعرض البلاغات دون تجاوز المساحة عند ${size.width}px', (
      tester,
    ) async {
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      final api = _ReportsApi();
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: ReportsPage(api: api, onOpenConversation: (_) {}),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('تصفية البلاغات'), findsOneWidget);
      expect(find.text('MRV-2026-0042'), findsOneWidget);
      expect(find.textContaining('مستخدم تجريبي'), findsOneWidget);
      if (size.width < 760) {
        expect(find.byType(DataTable), findsNothing);
        expect(find.text('تفاصيل البلاغ'), findsOneWidget);
      } else {
        expect(find.byType(DataTable), findsOneWidget);
      }
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('يطبق البحث عند ضغط زر البحث', (tester) async {
    tester.view.physicalSize = const Size(1280, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final api = _ReportsApi();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ReportsPage(api: api, onOpenConversation: (_) {}),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).first, 'MRV-2026-0042');
    await tester.tap(find.text('بحث'));
    await tester.pumpAndSettle();

    expect(api.calls.last.$1, '/api/admin/reports');
    expect(api.calls.last.$2, {
      'page': '0',
      'size': '25',
      'q': 'MRV-2026-0042',
    });
    expect(tester.takeException(), isNull);
  });

  testWidgets('يفتح تفاصيل البلاغ بعرض يناسب الهاتف', (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ReportsPage(
            api: _ReportsApi(),
            onOpenConversation: (_) {},
            focusReportId: 42,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('تفصيل البلاغ MRV-2026-0042'), findsOneWidget);
    expect(find.text('تفاصيل بلاغ تجريبي'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
