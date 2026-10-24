import 'package:flutter/material.dart';
import 'package:web/web.dart' as web;

/// ثيمات لوحة الإدارة — أربعة أوضاع داكنة بهوية خضراء موحّدة.
///
/// اللون accent يبقى ثابتًا في كل الأوضاع حتى لا تتعارض العناصر الملوّنة
/// داخل الصفحات، بينما تتغيّر خلفيات السطح والبطاقات والشريط الجانبي.
abstract final class AdminTheme {
  static const accent = Color(0xFF67D6A6);

  static const names = <String>['forest', 'midnight', 'ocean', 'graphite'];

  static const labels = <String, String>{
    'forest': 'الغابة الداكنة',
    'midnight': 'منتصف الليل',
    'ocean': 'المحيط',
    'graphite': 'الجرافيت',
  };

  static const _palettes = <String, _Palette>{
    'forest': _Palette(
      background: Color(0xFF08120E),
      surface: Color(0xFF111F18),
      border: Color(0xFF25382D),
      field: Color(0xFF0D1912),
    ),
    'midnight': _Palette(
      background: Color(0xFF080D16),
      surface: Color(0xFF111A27),
      border: Color(0xFF23314A),
      field: Color(0xFF0D1521),
    ),
    'ocean': _Palette(
      background: Color(0xFF051317),
      surface: Color(0xFF0C2228),
      border: Color(0xFF1D3E46),
      field: Color(0xFF0A1B20),
    ),
    'graphite': _Palette(
      background: Color(0xFF101113),
      surface: Color(0xFF1A1C20),
      border: Color(0xFF2E3238),
      field: Color(0xFF17191D),
    ),
  };

  static ThemeData of(String name) {
    final palette = _palettes[name] ?? _palettes['forest']!;
    final scheme = ColorScheme.fromSeed(
      seedColor: accent,
      brightness: Brightness.dark,
      surface: palette.surface,
    ).copyWith(primary: accent, surface: palette.surface);

    return ThemeData(
      useMaterial3: true,
      fontFamily: 'NotoSansArabic',
      colorScheme: scheme,
      scaffoldBackgroundColor: palette.background,
      cardColor: palette.surface,
      dividerColor: palette.border,
      appBarTheme: AppBarTheme(
        backgroundColor: palette.background,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        iconTheme: const IconThemeData(color: Colors.white70),
        actionsIconTheme: const IconThemeData(color: Colors.white70),
      ),
      navigationRailTheme: NavigationRailThemeData(
        backgroundColor: palette.surface,
        indicatorColor: accent.withValues(alpha: 0.16),
        selectedIconTheme: const IconThemeData(color: accent),
        selectedLabelTextStyle: const TextStyle(
          color: accent,
          fontWeight: FontWeight.w700,
        ),
      ),
      drawerTheme: DrawerThemeData(backgroundColor: palette.surface),
      snackBarTheme: const SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: palette.field,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: palette.border),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: palette.border),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: accent, width: 1.4),
        ),
      ),
    );
  }

  /// الوضع الافتراضي (يحافظ على المظهر السابق).
  static ThemeData get dark => of('forest');
}

class _Palette {
  const _Palette({
    required this.background,
    required this.surface,
    required this.border,
    required this.field,
  });

  final Color background;
  final Color surface;
  final Color border;
  final Color field;
}

/// حالة الثيم الحالية — تُحفظ في متصفح المستخدم وتبقى بعد إعادة الفتح.
class ThemeController extends ValueNotifier<String> {
  ThemeController() : super(_read());

  static const String storageKey = 'mrw_admin_theme';

  static String _read() {
    try {
      final saved = web.window.localStorage.getItem(storageKey);
      if (saved != null && AdminTheme.names.contains(saved)) return saved;
    } catch (_) {
      // التخزين قد يكون معطّلًا — نكمل بالافتراضي.
    }
    return 'forest';
  }

  void select(String name) {
    if (!AdminTheme.names.contains(name) || name == value) return;
    value = name;
    try {
      web.window.localStorage.setItem(storageKey, name);
    } catch (_) {
      // يبقى الثيم فعّالًا لهذه الجلسة فقط.
    }
  }
}

final ThemeController themeController = ThemeController();
