import 'dart:typed_data';

import 'package:admin_web/api.dart';
import 'package:admin_web/pages/bot_text_management_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class _TextApi implements AdminApi {
  final texts = <Map<String, dynamic>>[
    {
      'key': 'bot.INLINE_MESSAGES.followup_length',
      'label': 'رسالة طول المعلومات الإضافية',
      'content': 'النص الافتراضي',
      'category': 'user_facing',
    },
    {
      'key': 'bot.CATEGORY_INTROS.field',
      'label': 'مقدمة البلاغات الميدانية',
      'content': 'مقدمة',
      'category': 'user_facing',
    },
  ];

  @override
  Future<Map<String, dynamic>> get(
    String path, [
    Map<String, String>? query,
  ]) async => {'ok': true, 'texts': texts};

  @override
  Future<Map<String, dynamic>> post(
    String path,
    Map<String, dynamic> body,
  ) async {
    final item = texts.firstWhere((text) => text['key'] == body['key']);
    item['content'] = body['content'];
    return {'ok': true, 'applied': true};
  }

  @override
  Stream<Map<String, dynamic>> watchEvents() => const Stream.empty();

  @override
  Future<Uint8List> getBytes(String path) async => Uint8List(0);

  @override
  Future<Map<String, dynamic>> upload(
    String path, {
    required Map<String, String> fields,
    String? filename,
    Uint8List? bytes,
  }) async => {'ok': true};

  @override
  Future<Map<String, dynamic>> put(
    String path,
    Map<String, dynamic> body,
  ) async => {'ok': true};

  @override
  Future<Map<String, dynamic>> del(
    String path,
    Map<String, dynamic> body,
  ) async => {'ok': true};

  @override
  String photoUrl(num reportId, {Object? bust}) => '';

  @override
  Future<void> download(String path) async {}
}

void main() {
  testWidgets('يعرض النصوص كشجرة ويتيح البحث والحفظ', (tester) async {
    final api = _TextApi();
    await tester.pumpWidget(MaterialApp(home: BotTextManagementPage(api: api)));
    await tester.pumpAndSettle();

    expect(find.text('رسائل وتفاعلات البوت'), findsOneWidget);
    await tester.tap(find.text('رسائل وتفاعلات البوت'));
    await tester.pumpAndSettle();
    expect(find.text('رسالة طول المعلومات الإضافية'), findsOneWidget);

    await tester.tap(find.text('رسالة طول المعلومات الإضافية'));
    await tester.pumpAndSettle();
    expect(find.text('حفظ وتطبيق على البوت'), findsOneWidget);
    await tester.enterText(find.byType(TextField).last, 'نص محدث');
    await tester.tap(find.text('حفظ وتطبيق على البوت'));
    await tester.pumpAndSettle();
    expect(api.texts.first['content'], 'نص محدث');
    expect(find.text('تم الحفظ والتطبيق على البوت مباشرة'), findsOneWidget);

    await tester.enterText(find.byType(TextField).first, 'ميدانية');
    await tester.pumpAndSettle();
    expect(find.text('مقدمات الأقسام'), findsOneWidget);
    expect(find.text('مقدمة البلاغات الميدانية'), findsOneWidget);
  });
}
