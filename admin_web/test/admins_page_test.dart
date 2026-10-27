import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:admin_web/api.dart';
import 'package:admin_web/pages/admins_page.dart';

class _AdminApi extends Fake implements AdminApi {
  Map<String, dynamic>? created;

  @override
  Future<Map<String, dynamic>> get(
    String path, [
    Map<String, String>? query,
  ]) async => {'ok': true, 'items': <Map<String, dynamic>>[]};

  @override
  Future<Map<String, dynamic>> post(
    String path,
    Map<String, dynamic> body,
  ) async {
    created = body;
    return {
      'ok': true,
      'token': 'one-time-admin-token',
      'item': {'id': 'admin-1'},
    };
  }
}

void main() {
  testWidgets('creates an admin and displays its one-time token', (
    tester,
  ) async {
    final api = _AdminApi();
    await tester.pumpWidget(
      MaterialApp(home: AdminsPage(api: api)),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.text('إضافة مشرف'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).first, 'مشرف جديد');
    await tester.tap(find.text('إنشاء الرمز'));
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));

    expect(find.text('one-time-admin-token'), findsOneWidget);
    expect(api.created?['role'], 'reviewer');
    expect(api.created?['permissions'], containsAll(['read', 'review']));

    await tester.tap(find.text('حفظت الرمز'));
    await tester.pumpAndSettle();
    expect(find.text('المشرفون'), findsOneWidget);
  });
}
