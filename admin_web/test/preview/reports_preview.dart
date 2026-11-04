// معاينة بصرية لصفحة البلاغات — تُنتج صورًا مرجعية للمراجعة.
//
// ليست اختبارًا في CI: اسم الملف لا ينتهي بـ`_test.dart`، فتشغيل
// `flutter test` المعتاد لا يلتقطه. شغّله صراحة عند تغيير التصميم:
//
//   flutter test test/preview/reports_preview.dart --update-goldens
//
// ثم افتح الصور في `test/preview/goldens/`.
import 'dart:typed_data';

import 'package:admin_web/api.dart';
import 'package:admin_web/pages/reports_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

const _muted = Color(0xFF9AA7A0);
const _accent = Color(0xFF67D6A6);
const _font = 'NotoSansArabic';

/// تحميل خط الواجهة الفعلي حتى تظهر العربية في الصورة المرجعية.
Future<void> _loadFonts() async {
  final loader = FontLoader(_font);
  loader.addFont(rootBundle.load('fonts/NotoSansArabic-Regular.ttf'));
  loader.addFont(rootBundle.load('fonts/NotoSansArabic-Bold.ttf'));
  await loader.load();
}

ThemeData _previewTheme() {
  final scheme = ColorScheme.fromSeed(
    seedColor: _accent,
    brightness: Brightness.dark,
    surface: const Color(0xFF111F18),
  ).copyWith(
    primary: _accent,
    surface: const Color(0xFF111F18),
    outline: const Color(0xFF25382D),
    onSurfaceVariant: _muted,
  );
  return ThemeData(
    useMaterial3: true,
    fontFamily: _font,
    colorScheme: scheme,
    scaffoldBackgroundColor: const Color(0xFF08120E),
    cardColor: const Color(0xFF111F18),
  );
}

class _PreviewApi implements AdminApi {
  _PreviewApi(this.items);

  final List<Map<String, dynamic>> items;

  @override
  Future<Map<String, dynamic>> get(
    String path, [
    Map<String, String>? query,
  ]) async {
    if (path == '/api/admin/vtypes') {
      return {
        'items': ['سلوك فردي', 'إهمال واجب', 'تجاوز تعليمات'],
      };
    }
    if (path == '/api/admin/meta') {
      return {
        'admin_ids': [11, 12],
        'vtypes': ['سلوك فردي', 'إهمال واجب', 'تجاوز تعليمات'],
      };
    }
    if (path == '/api/admin/reports') {
      return {'total': items.length, 'items': items};
    }
    return {'ok': true};
  }

  @override
  Stream<Map<String, dynamic>> watchEvents() => const Stream.empty();

  @override
  Future<Uint8List> getBytes(String path) async => Uint8List(0);

  @override
  Future<Map<String, dynamic>> post(
    String path,
    Map<String, dynamic> body,
  ) async => {'ok': true};

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

Map<String, dynamic> _report({
  required int id,
  required String seq,
  required String name,
  required String status,
  required String priority,
  required String type,
  required String createdAt,
  String? dueAt,
  int? assignedTo,
  bool photo = true,
}) => {
  'id': id,
  'seq': seq,
  'full_name': name,
  'user_id': 155000 + id,
  'username': 'user_$id',
  'status': status,
  'priority': priority,
  'violation_type': type,
  'created_at': createdAt,
  'user_last_at': createdAt,
  'has_photo': photo,
  'photo_file_id': photo ? 'file_$id' : '',
  'assigned_to': assignedTo,
  'due_at': dueAt,
  'description': 'وصف تجريبي للبلاغ رقم $id يستخدم لمعاينة التصميم فقط.',
};

List<Map<String, dynamic>> _sampleReports() => [
  _report(
    id: 43,
    seq: 'MRV-2026-0043',
    name: 'أحمد سالم',
    status: 'جديد',
    priority: 'urgent',
    type: 'سلوك فردي',
    createdAt: '2026-11-04T08:10:00',
    dueAt: '2026-11-01T09:00:00',
  ),
  _report(
    id: 42,
    seq: 'MRV-2026-0042',
    name: 'مستخدم تجريبي',
    status: 'قيد المراجعة',
    priority: 'high',
    type: 'إهمال واجب',
    createdAt: '2026-11-04T07:30:00',
    assignedTo: 11,
    dueAt: '2026-11-09T09:00:00',
  ),
  _report(
    id: 41,
    seq: 'MRV-2026-0041',
    name: 'خالد ناصر',
    status: 'جديد',
    priority: 'normal',
    type: 'تجاوز تعليمات',
    createdAt: '2026-11-03T19:05:00',
    photo: false,
  ),
  _report(
    id: 40,
    seq: 'MRV-2026-0040',
    name: 'عبدالله محمد',
    status: 'معتمد',
    priority: 'low',
    type: 'سلوك فردي',
    createdAt: '2026-11-02T12:40:00',
    assignedTo: 12,
  ),
];

Future<void> _pump(
  WidgetTester tester, {
  required Size size,
}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);

  await tester.pumpWidget(
    MaterialApp(
      theme: _previewTheme(),
      debugShowCheckedModeBanner: false,
      home: Scaffold(
        body: ReportsPage(
          api: _PreviewApi(_sampleReports()),
          onOpenConversation: (_) {},
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  setUpAll(_loadFonts);

  testWidgets('preview: reports desktop table', (tester) async {
    await _pump(tester, size: const Size(1280, 900));
    await expectLater(
      find.byType(ReportsPage),
      matchesGoldenFile('goldens/reports_desktop.png'),
    );
  });

  testWidgets('preview: reports mobile cards', (tester) async {
    await _pump(tester, size: const Size(420, 900));
    await expectLater(
      find.byType(ReportsPage),
      matchesGoldenFile('goldens/reports_mobile.png'),
    );
  });
}
