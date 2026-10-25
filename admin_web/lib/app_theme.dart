import 'dart:convert';

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

  static ThemeData get dark => build();

  static ThemeData get light => build(mode: ThemeMode.light);

  static ThemeData of(String name) => build(paletteName: name);

  static ThemeData build({
    ThemeMode mode = ThemeMode.dark,
    String fontFamily = 'NotoSansArabic',
    bool compact = false,
    Color seed = accent,
    String paletteName = 'forest',
  }) {
    final isDark = mode != ThemeMode.light;
    final palette = _palettes[paletteName] ?? _palettes['forest']!;
    final background = isDark ? palette.background : const Color(0xFFF2F5F3);
    final surface = isDark ? palette.surface : const Color(0xFFFFFFFF);
    final border = isDark ? palette.border : const Color(0xFFD7E0DA);
    final field = isDark ? palette.field : const Color(0xFFF4F7F5);
    final scheme = ColorScheme.fromSeed(
      seedColor: seed,
      brightness: isDark ? Brightness.dark : Brightness.light,
      surface: surface,
    ).copyWith(primary: seed, surface: surface);
    return ThemeData(
      useMaterial3: true,
      fontFamily: fontFamily.isEmpty ? null : fontFamily,
      colorScheme: scheme,
      scaffoldBackgroundColor: background,
      cardColor: surface,
      dividerColor: border,
      visualDensity: compact ? VisualDensity.compact : VisualDensity.standard,
      appBarTheme: AppBarTheme(
        backgroundColor: background,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        iconTheme: IconThemeData(
          color: isDark ? Colors.white70 : const Color(0xFF36433B),
        ),
        actionsIconTheme: IconThemeData(
          color: isDark ? Colors.white70 : const Color(0xFF36433B),
        ),
      ),
      navigationRailTheme: NavigationRailThemeData(
        backgroundColor: surface,
        indicatorColor: seed.withValues(alpha: 0.16),
        selectedIconTheme: IconThemeData(color: seed),
        selectedLabelTextStyle: TextStyle(
          color: seed,
          fontWeight: FontWeight.w700,
        ),
      ),
      drawerTheme: DrawerThemeData(backgroundColor: surface),
      snackBarTheme: const SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: field,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: border),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: border),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: seed, width: 1.4),
        ),
      ),
    );
  }
}

abstract final class AdminPreferences {
  static const _key = 'mrw_admin_visual_preferences';
  static const _soundKey = 'mrw_admin_sound_preferences';

  static Map<String, dynamic> load() {
    try {
      final raw = web.window.localStorage.getItem(_key);
      final parts = raw?.split('|') ?? const <String>[];
      final visual = parts.length == 5
          ? {
              'mode': parts[0],
              'scale': double.tryParse(parts[1]) ?? 1.0,
              'font': parts[2],
              'compact': parts[3] == '1',
              'seed': int.tryParse(parts[4]) ?? AdminTheme.accent.toARGB32(),
            }
          : <String, dynamic>{};
      final soundRaw = web.window.localStorage.getItem(_soundKey);
      final sound = soundRaw == null
          ? <String, dynamic>{}
          : jsonDecode(soundRaw) as Map<String, dynamic>;
      return {..._defaults, ...visual, ...sound};
    } catch (_) {
      return _defaults;
    }
  }

  static Map<String, dynamic> get _defaults => {
    'mode': 'dark',
    'scale': 1.0,
    'font': 'NotoSansArabic',
    'compact': false,
    'seed': AdminTheme.accent.toARGB32(),
    'soundEnabled': true,
    'soundUnlocked': false,
    'sound_report_new': 'report',
    'sound_report_urgent': 'alert',
    'sound_user_blocked': 'alert',
    'sound_user_message': 'message',
    'sound_emergency': 'alert',
    'sound_heartbeat_fail': 'alert',
  };

  static void save(Map<String, dynamic> values) {
    web.window.localStorage.setItem(
      _key,
      [
        values['mode'] ?? 'dark',
        values['scale'] ?? 1.0,
        values['font'] ?? 'NotoSansArabic',
        values['compact'] == true ? '1' : '0',
        values['seed'] ?? AdminTheme.accent.toARGB32(),
      ].join('|'),
    );
    web.window.localStorage.setItem(
      _soundKey,
      jsonEncode({
        'soundEnabled': values['soundEnabled'] ?? true,
        'soundUnlocked': values['soundUnlocked'] == true,
        for (final type in const [
          'report_new',
          'report_urgent',
          'user_blocked',
          'user_message',
          'emergency',
          'heartbeat_fail',
        ])
          'sound_$type':
              values['sound_$type'] ??
              (type == 'report_new'
                  ? 'report'
                  : type == 'user_message'
                  ? 'message'
                  : 'alert'),
      }),
    );
  }
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
