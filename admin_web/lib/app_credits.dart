/// حقوق النشر والإصدار — مصدر واحد لكل الواجهة.
///
/// الإصدار يُمرَّر وقت البناء عبر `--dart-define=APP_VERSION=...`
/// (يفعل ذلك build_release.ps1 تلقائيًا بتاريخ البناء).
library;

import 'package:flutter/material.dart';

class AppCredits {
  const AppCredits._();

  static const String project = 'منظومة استقبال البلاغات العسكرية';
  static const String author = 'ABU-AZZAM-COM / Azzam';
  static const String copyright = '© 2026 — جميع الحقوق محفوظة';
  static const String repo = 'https://github.com/ABU-AZZAM-COM/mrw-bot';

  static const String version = String.fromEnvironment(
    'APP_VERSION',
    defaultValue: '2.7.20261026.1',
  );

  static String get versionLabel => 'v$version';
  static String get footer => '$copyright · $author';
}

/// نافذة الحقوق والإصدار — تُفتح من شريط التطبيق أو شاشة الدخول.
void showCredits(BuildContext context) {
  showAboutDialog(
    context: context,
    applicationName: AppCredits.project,
    applicationVersion: AppCredits.version,
    applicationIcon: const Icon(Icons.shield_outlined, size: 42),
    applicationLegalese: '${AppCredits.copyright}\n${AppCredits.author}',
    children: [
      const SizedBox(height: 8),
      SelectableText(
        AppCredits.repo,
        style: TextStyle(
          fontSize: 12,
          color: Theme.of(context).colorScheme.onSurfaceVariant,
        ),
      ),
    ],
  );
}
