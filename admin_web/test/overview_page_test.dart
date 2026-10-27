import 'dart:typed_data';

import 'package:admin_web/api.dart';
import 'package:admin_web/pages/overview_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class _OverviewApi implements AdminApi {
  _OverviewApi({this.failStats = false});

  final bool failStats;
  final calls = <(String, Map<String, String>?)>[];

  @override
  Future<Map<String, dynamic>> get(
    String path, [
    Map<String, String>? query,
  ]) async {
    calls.add((path, query));
    if (path == '/api/admin/overview') {
      return {
        'ok': true,
        'bot_ready': true,
        'emergency': false,
        'counts': {
          'total': 24,
          'last24': 4,
          'جديد': 3,
          'قيد المراجعة': 5,
          'معتمد': 14,
          'مرفوض': 2,
        },
        'workflow': {'urgent': 2, 'overdue': 1, 'unassigned': 3},
        'users': {'total': 18, 'blocked': 1},
      };
    }
    if (path == '/api/admin/stats') {
      if (failStats) throw ApiException(503, 'stats_unavailable');
      final today = DateTime.now();
      final date =
          '${today.year.toString().padLeft(4, '0')}-'
          '${today.month.toString().padLeft(2, '0')}-'
          '${today.day.toString().padLeft(2, '0')}';
      return {
        'daily7': [
          {'day': date, 'n': 4},
        ],
      };
    }
    if (path == '/api/admin/reports') {
      final filter = query?['priority'] == 'urgent'
          ? 'urgent'
          : query?['overdue'] == 'true'
          ? 'overdue'
          : query?['unassigned'] == 'true'
          ? 'unassigned'
          : 'recent';
      final report = {
        'id': 42,
        'seq': 'MRV-2026-0042',
        'full_name': 'مستخدم تجريبي',
        'status': 'جديد',
        'priority': 'urgent',
        'created_at': DateTime.now().toIso8601String(),
      };
      return {
        'ok': true,
        'total': 1,
        'items': [
          report,
          {'seq': filter},
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
  testWidgets('مركز القيادة يعرض المؤشرات والبلاغات ويتنقل للأقسام', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1280, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final api = _OverviewApi();
    final destinations = <int>[];
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: OverviewPage(api: api, onNavigate: destinations.add),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('مركز قيادة البلاغات'), findsOneWidget);
    expect(find.text('إجمالي البلاغات'), findsOneWidget);
    expect(find.text('24'), findsOneWidget);
    expect(find.text('حركة البلاغات'), findsOneWidget);
    expect(find.text('توزيع الحالات'), findsOneWidget);
    expect(find.text('تحتاج انتباهك'), findsOneWidget);

    await tester.tap(find.text('التحليلات'));
    expect(destinations, [10]);
    expect(
      api.calls.where((call) => call.$1 == '/api/admin/reports'),
      hasLength(4),
    );
    await tester.scrollUntilVisible(
      find.text('أحدث البلاغات'),
      300,
      scrollable: find.descendant(
        of: find.byType(ListView),
        matching: find.byType(Scrollable),
      ),
    );
    expect(find.text('أحدث البلاغات'), findsOneWidget);
    expect(find.textContaining('MRV-2026-0042'), findsOneWidget);
  });

  testWidgets('يعرض تنبيهًا مفهومًا عند فشل قسم تحليلات اختياري', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1280, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final api = _OverviewApi(failStats: true);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: OverviewPage(api: api)),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.textContaining('بعض الأقسام لم تُحمّل'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.text('حركة البلاغات'),
      300,
      scrollable: find.descendant(
        of: find.byType(ListView),
        matching: find.byType(Scrollable),
      ),
    );
    expect(find.text('حركة البلاغات'), findsOneWidget);
    expect(find.textContaining('بيانات الرسم غير متاحة'), findsOneWidget);
  });
}
