import 'package:flutter/material.dart';

abstract final class AdminTheme {
  static const accent = Color(0xFF67D6A6);
  static const background = Color(0xFF08120E);
  static const surface = Color(0xFF111F18);
  static const border = Color(0xFF25382D);

  static ThemeData get dark => ThemeData(
    useMaterial3: true,
    fontFamily: 'NotoSansArabic',
    colorScheme: ColorScheme.fromSeed(
      seedColor: accent,
      brightness: Brightness.dark,
      surface: surface,
    ),
    scaffoldBackgroundColor: background,
    dividerColor: border,
    snackBarTheme: const SnackBarThemeData(behavior: SnackBarBehavior.floating),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: const Color(0xFF0D1912),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: const BorderSide(color: border),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: const BorderSide(color: border),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: const BorderSide(color: accent, width: 1.4),
      ),
    ),
  );
}
