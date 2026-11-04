/// رموز التصميم المشتركة للوحة الإدارة.
///
/// كل الصفحات تقرأ من هنا بدل كتابة أرقام موزّعة في الكود، فتصبح المسافات
/// والحواف والحركة متسقة، ويصبح تعديل المظهر مركزيًا في مكان واحد.
library;

import 'package:flutter/material.dart';

abstract final class AdminTokens {
  // ---------------------------------------------------------------
  // المسافات (شبكة رباعية)
  // ---------------------------------------------------------------
  static const s1 = 4.0;
  static const s2 = 8.0;
  static const s3 = 12.0;
  static const s4 = 16.0;
  static const s5 = 20.0;
  static const s6 = 24.0;
  static const s8 = 32.0;

  /// حافة الصفحة الأفقية.
  static const pageInset = s5;

  /// الفاصل الرأسي بين الأقسام.
  static const sectionGap = s4;

  /// حافة داخل البطاقات.
  static const cardInset = s4;

  static const gapTight = s1;
  static const gapSmall = s2;
  static const gapMedium = s3;

  // ---------------------------------------------------------------
  // الحواف
  // ---------------------------------------------------------------
  static const cardRadius = 18.0;
  static const heroRadius = 24.0;
  static const controlRadius = 12.0;
  static const chipRadius = 999.0;

  // ---------------------------------------------------------------
  // الحركة
  // ---------------------------------------------------------------
  static const fast = Duration(milliseconds: 120);
  static const normal = Duration(milliseconds: 220);
  static const slow = Duration(milliseconds: 320);

  /// منحنى حركة موحّد: بداية سريعة ونهاية ناعمة.
  static const curve = Curves.easeOutCubic;

  // ---------------------------------------------------------------
  // أبعاد مضبوطة
  // ---------------------------------------------------------------
  static const toolbarHeight = 56.0;
  static const rowHeight = 52.0;
  static const iconSmall = 18.0;
  static const iconMedium = 22.0;

  /// حد أقصى لعرض محتوى القراءة حتى لا يتمدد النص على الشاشات العريضة.
  static const readingMaxWidth = 760.0;
}
