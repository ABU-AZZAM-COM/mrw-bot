import 'package:flutter/material.dart';
import 'package:web/web.dart' as web;

abstract final class AdminTheme {
  static const accent = Color(0xFF67D6A6);
  static const background = Color(0xFF08120E);
  static const surface = Color(0xFF111F18);
  static const border = Color(0xFF25382D);

  static ThemeData get dark => build();

  static ThemeData get light => build(mode: ThemeMode.light);

  static ThemeData build({
    ThemeMode mode = ThemeMode.dark,
    String fontFamily = 'NotoSansArabic',
    bool compact = false,
    Color seed = accent,
  }) {
    final isDark = mode != ThemeMode.light;
    final borderColor = isDark ? border : const Color(0xFFD7E0DA);
    final surfaceColor = isDark ? surface : const Color(0xFFFFFFFF);
    final fillColor = isDark
        ? const Color(0xFF0D1912)
        : const Color(0xFFF4F7F5);
    final base = ThemeData(
      useMaterial3: true,
      fontFamily: fontFamily.isEmpty ? null : fontFamily,
      colorScheme: ColorScheme.fromSeed(
        seedColor: seed,
        brightness: isDark ? Brightness.dark : Brightness.light,
        surface: surfaceColor,
      ),
      scaffoldBackgroundColor: isDark ? background : const Color(0xFFF2F5F3),
      dividerColor: borderColor,
      visualDensity: compact ? VisualDensity.compact : VisualDensity.standard,
      snackBarTheme: const SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: fillColor,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: borderColor),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: borderColor),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: seed, width: 1.4),
        ),
      ),
    );
    return base;
  }
}

abstract final class AdminPreferences {
  static const _key = 'mrw_admin_visual_preferences';

  static Map<String, dynamic> load() {
    try {
      final raw = web.window.localStorage.getItem(_key);
      if (raw == null || raw.isEmpty) return _defaults;
      final parts = raw.split('|');
      if (parts.length != 5) return _defaults;
      return {
        'mode': parts[0],
        'scale': double.tryParse(parts[1]) ?? 1.0,
        'font': parts[2],
        'compact': parts[3] == '1',
        'seed': int.tryParse(parts[4]) ?? AdminTheme.accent.toARGB32(),
      };
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
  }
}
