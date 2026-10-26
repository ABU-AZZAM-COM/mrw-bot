# لوحة الإدارة — Flutter Web

لوحة تحكم كاملة لبوت بلاغات المخالفات العسكرية، مبنية بـ Flutter Web وتتصل
بـ API الإدارة الحي على خادم البوت نفسه.

## الروابط

- **على Render:** `https://mrw-bot.onrender.com/admin/`
- **محليًا (سطح المكتب):** شغّل `launch_desktop.bat` من جذر المشروع.

## الدخول

تُفتح اللوحة على شاشة توكن الإدارة. التوكن هو قيمة `ADMIN_API_TOKEN` في `.env`
وعلى Render. يُخزَّن في متصفحك فقط (`localStorage`).

## البناء

```powershell
cd admin_web
.\build_release.ps1
```

السكربت:

1. ينشئ رقم إصدار بتاريخ اليوم (`2.7.YYYYMMDD`).
2. يبني Flutter Web بـ `--base-href=/admin/`.
3. يمرّر الإصدار عبر `--dart-define=APP_VERSION=...`.
4. ينسخ المخرجات إلى `dist/` (المجلد الذي يخدمه خادم البوت).
5. يزيد رقمًا فرعيًا إذا تكرر بناء إصدار في اليوم نفسه.

أضف ملخص الإصدار وميزاته إلى أعلى قائمة `releaseNotes` في
`lib/release_notes.dart` عند تجهيز كل إصدار؛ تعرض صفحة «ما الجديد» السجل
بالإصدارات وتُبرز الإصدار المثبت.

للاتصال بخادم مختلف وقت البناء:

```powershell
$env:API_BASE = "https://example.com"
.\build_release.ps1
```

بدون `API_BASE` تستخدم اللوحة نفس منبع الصفحة (وهو المطلوب على Render).

## الثيمات

أربعة أوضاع داكنة من زر لوحة الألوان في شريط التطبيق:

- الغابة الداكنة (افتراضي)
- منتصف الليل
- المحيط
- الجرافيت

الاختيار محفوظ في `localStorage` لكل متصفح، واللون الأخضر `#67D6A6` يبقى
موحّدًا في كل الأوضاع.

## الصفحات

| الصفحة | الملف |
|---|---|
| نظرة عامة | `lib/pages/overview_page.dart` |
| البلاغات | `lib/pages/reports_page.dart` |
| المتبلّغون | `lib/pages/reporters_page.dart` |
| التشغيل | `lib/pages/operations_page.dart` |
| الرسائل | `lib/pages/messaging_page.dart` |
| التصنيفات | `lib/pages/types_page.dart` |
| سجل التدقيق | `lib/pages/audit_page.dart` |
| المُقيَّدون | `lib/pages/blocked_page.dart` |
| الدخول والهيكل | `lib/main.dart` و `lib/admin_shell.dart` |
| عميل الـ API | `lib/web_api.dart` |
| الثيمات | `lib/app_theme.dart` |
| الحقوق والإصدار | `lib/app_credits.dart` |
| سجل الإصدارات | `lib/release_notes.dart` و `lib/pages/whats_new_page.dart` |

## الحقوق

© 2026 — ABU-AZZAM-COM / Azzam — جميع الحقوق محفوظة.
