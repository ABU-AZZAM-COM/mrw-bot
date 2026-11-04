# بنية المشروع بعد إعادة الهيكلة

يشرح هذا الملف التقسيم الجديد للوحدات الكبرى الثلاث، وكيف تُربط أجزاؤها،
وما تبقّى من عمل مقصود تأجيله.

## 1) واجهة الإدارة (`api/`)

كان الملف `api/admin_api.py` يحتوي 5,703 سطر و115 مساراً. الآن:

| الوحدة | المسؤولية |
|---|---|
| `admin_api.py` | ربط فقط: middleware المصادقة + استدعاء الراوترات + جسور البوت |
| `admin_auth.py` | المصادقة، الأدوار، كلمات المرور، الجلسات |
| `rate_limit.py` | حماية محاولات المصادقة المتكررة (429 + Retry-After) |
| `routers/notifications.py` | الإشعارات وبثّ الأحداث (SSE) |
| `routers/reports.py` | البلاغات وسير العمل والملاحظات |
| `routers/bot_texts.py` | نصوص البوت |
| `routers/admins.py` | حسابات الإدارة والجلسات |
| `routers/verified_reporters.py` | توثيق المراسلين والوثائق |
| `routers/users.py` | المستخدمون والمحادثات والرسائل |
| `routers/broadcast.py` | البثّ الجماعي والمنشورات |
| `routers/bot_menu.py` · `routers/bot_profile.py` | أزرار/أوامر/قوائم البوت · الملف التعريفي |
| `routers/osint.py` · `routers/audit.py` | مصادر OSINT · سجل التدقيق |
| `routers/platform.py` | الإصدار، الصحة، الإحصاءات، النسخ الاحتياطي، التصدير |
| `admin_dashboard.py` | تحويل `/dashboard` إلى `/admin/` + عقد `/api/admin/dashboard` |

**نمط الربط:** كل راوتر يُصدّر `register(app, *, db_of, bad)` ويستقبل مساعدات
`admin_api` صراحةً. ما يبقى في `admin_api` من دوال مشتركة يُربط عبر أغلفة
تحلّ الاسم وقت الطلب لا وقت التركيب، حفاظاً على قابلية الاستبدال في الاختبارات
ولمنع الاستيراد الدائري.

## 2) طبقة البيانات (`database/`)

كان `database/__init__.py` يحتوي 5,216 سطر و290 دالة في صنف واحد. الآن:

```python
class Database(
    VerificationMixin, BroadcastMixin, NotificationsMixin, AuditMixin,
    OsintMixin, ChatMixin, UsersMixin, ReportsMixin, FollowupsMixin,
    SettingsMixin, AdminAuthMixin, MaintenanceMixin,
):
```

`database/__init__.py` (757 سطر) يحتفظ فقط بمخطط الجداول ودورة الحياة
(`__init__`, `_migrate`, `health_check`, `close`, `path`, `export_sqlite_snapshot`).
الثوابت وأدوات تحويل الصفوف في `database/_shared.py`.

**قاعدة الإضافة:** أي أساليب جديدة تُضاف إلى الوحدة المطابقة لنطاقها، لا إلى
`__init__.py`.

## 3) البوت (`bot/`)

كان `bot/handlers.py` يحتوي 3,612 سطر. الآن:

| الوحدة | المسؤولية |
|---|---|
| `handlers.py` (502) | بناء `ConversationHandler`، تسجيل الأوامر، أدوات صغيرة |
| `report_flow.py` | تدفق إرسال البلاغ كاملاً |
| `verification_flow.py` | تدفق توثيق المراسلين |
| `app_flow.py` | تدفق التطبيق المصغّر (Mini App) |
| `callbacks.py` | معالجات أزرار المستخدم |
| `commands.py` | الأوامر والدعم والمتابعة والاستئناف |
| `helpers.py` | المساعدات المشتركة وثوابت مراحل المحادثة |

## 4) الاختبارات

| المنصّة | الأمر | الحالة |
|---|---|---|
| Python | `python -m unittest discover -s tests` | 138 اختباراً |
| Flutter | `flutter test` داخل `admin_web/` | 30 اختباراً |
| تحليل Flutter | `flutter analyze` | 0 أخطاء، 0 تحذيرات |

> ملاحظة تشغيل: بعض الاختبارات تكتب ملفات مؤقتة؛ إن كان مجلد Temp محمياً
> وجّه `TEMP`/`TMP` إلى مسار قابل للكتابة.

## 5) أدوات مساعدة في `scripts/`

- `check_router_names.py` — يكشف أسماء غير معرّفة في أي وحدة (فحص ساكن).
- `move_db_methods.py` — نقل أساليب مختارة إلى mixin.
- `extract_db_mixin.py` — استخراج كتلة متصلة إلى mixin.

## 6) backlog مقصود (لم يُنفَّذ بعد)

1. ~~**توحيد واجهات الإدارة الثلاث**~~ — أُنجز: `admin_web/` (Flutter) هي الواجهة
   الوحيدة على `/admin/`، و`/dashboard` صار تحويلاً دائماً إليها، وبقيت
   `/api/admin/dashboard` عقداً برمجياً فقط. `desktop_panel/` بديل محلي اختياري
   غير مطوَّر ولا يُحذف.
2. ~~**تعميم نظام التصميم**~~ — أُنجز على `overview` و`reports` و`analytics`
   و`audit` و`health` و`osint` و`preferences`: رموز `AdminTokens`، هياكل تحميل
   `SkeletonBox`، شارات حالة موحّدة، وحشو صفحة واحد. الصفحات الكبيرة المتبقية
   (`messaging`, `verified_reporters`, `bot_*`) تُرحَّل عند الحاجة إليها.
3. **`bot/texts.py`** (1,389 سطراً) — كتالوج نصوص؛ يمكن تقسيمه حسب الشاشة عند
   الحاجة، وهو منخفض المخاطر لكنه غير عاجل.
4. **`api/routers/bot_config.py`** — أُنجز تقسيمه إلى `bot_menu` و`bot_profile`.
5. **تنظيف ملفات الجذر المؤقتة** — `_tmp_*.py`, `tg_*.py`, وملفات `deploy_*.txt`
   القديمة؛ لا تؤثر على التشغيل لكنها تشوّش المستودع.
