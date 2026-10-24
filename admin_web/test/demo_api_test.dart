import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:admin_web/demo_api.dart';
import 'package:admin_web/pages/analytics_page.dart';
import 'package:admin_web/pages/health_page.dart';

void main() {
  test(
    'demo API provides sample dashboard data without remote credentials',
    () async {
      final api = DemoApi();

      final overview = await api.get('/api/admin/overview');
      expect(overview['ok'], isTrue);
      expect(overview['bot_ready'], isFalse);
      expect(overview['counts']['total'], 4);

      final reports = await api.get('/api/admin/reports');
      expect(reports['total'], 4);
      expect((reports['items'] as List).first['seq'], 'DEMO-001');

      final blocked = await api.get('/api/admin/blocked-users');
      expect(blocked['total'], 1);
      expect((blocked['items'] as List).first['reason'], 'مثال حجب للتدريب');
    },
  );

  test('demo actions mutate only their in-memory sample data', () async {
    final api = DemoApi();

    await api.post('/api/admin/reports/101/status', {
      'status': 'rejected',
      'reason': 'سبب تدريبي',
    });
    final report = await api.get('/api/admin/reports/101');
    expect(report['item']['status'], 'مرفوض');

    await api.post('/api/admin/system/emergency', {'on': true});
    final overview = await api.get('/api/admin/overview');
    expect(overview['emergency'], isTrue);

    final audit = await api.get('/api/admin/audit');
    expect(audit['items'], hasLength(2));
    expect(
      (audit['items'] as List).every(
        (entry) => entry['details'].toString().contains('لا أثر على البوت'),
      ),
      isTrue,
    );
  });

  test(
    'demo API supports report workflow and operational settings locally',
    () async {
      final api = DemoApi();

      await api.put('/api/admin/reports/101/workflow', {
        'priority': 'urgent',
        'assigned_to': 1557500001,
        'due_at': '2026-12-01T10:00:00+03:00',
      });
      await api.post('/api/admin/reports/101/followup', {
        'note': 'يرجى إرسال تفاصيل إضافية',
      });
      final report = await api.get('/api/admin/reports/101');
      expect(report['item']['priority'], 'urgent');
      expect(report['item']['assigned_to'], 1557500001);
      expect(report['item']['followup_requested_at'], isNotNull);

      await api.put('/api/admin/settings', {
        'max_description_length': 1800,
        'max_photo_mb': 8,
        'max_reports_per_day': 6,
        'max_reports_per_minute': 3,
      });
      final settings = await api.get('/api/admin/settings');
      expect(settings['items']['max_description_length'], 1800);
      expect((await api.get('/api/admin/session'))['role'], 'owner');
    },
  );

  test(
    'demo data supports the analytics and diagnostics API contract',
    () async {
      final api = DemoApi();
      final stats = await api.get('/api/admin/stats');
      final audit = await api.get('/api/admin/audit', {'limit': '1'});
      final overview = await api.get('/api/admin/overview');

      expect(stats['daily7'], isA<List>());
      expect(stats['by_type'], isA<List>());
      expect(stats['top_users'], isA<List>());
      expect(audit['items'], isA<List>());
      expect(overview['bot_ready'], isFalse);
      expect(overview['emergency'], isFalse);
    },
  );

  testWidgets('analytics and health sections show training data clearly', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1200, 1800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final api = DemoApi();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: AnalyticsPage(api: api)),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('إجمالي البلاغات'), findsOneWidget);
    expect(find.text('التصنيفات الأكثر ورودًا'), findsOneWidget);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: HealthPage(api: api)),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('حالة الاتصالات والخدمات'), findsOneWidget);
    expect(find.text('بوت Telegram'), findsOneWidget);
    expect(find.text('غير جاهز'), findsOneWidget);
  });
}
