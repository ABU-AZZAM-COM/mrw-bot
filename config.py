"""
الإعدادات المركزية للمشروع.

- يقرأ القيم السرية من ملف .env (لا يُكتب التوكن في الكود أبدًا).
- يجهّز مجلدات العمل (data / media / exports / logs) تلقائيًا.
- أي خطأ في الإعدادات يظهر برسالة عربية واضحة فورًا عند التشغيل.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# المجلد الجذر للمشروع (الذي يضم هذا الملف)
BASE_DIR = Path(__file__).resolve().parent

# تحميل متغيرات البيئة من ملف .env إن وُجد
load_dotenv(BASE_DIR / ".env")

# ------------------------------------------------------------------
# توكن البوت (يُحصل عليه من @BotFather)
# ------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

if not BOT_TOKEN or BOT_TOKEN.startswith("ضع_هنا"):
    print("=" * 62)
    print("خطأ: توكن البوت غير مضبوط!")
    print("1) انسخ ملف .env.example وأعد تسميته إلى .env")
    print("2) ضع التوكن الذي حصلت عليه من @BotFather في BOT_TOKEN")
    print("=" * 62)
    raise SystemExit(1)


# ------------------------------------------------------------------
# معرفات المشرفين (Telegram User ID) — مفصولة بفواصل
# ------------------------------------------------------------------
def _parse_admin_ids(raw: str) -> tuple[int, ...]:
    """تحويل نص ADMIN_IDS إلى مجموعة أرقام صحيحة مع تجاهل القيم الخاطئة."""
    ids: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            print(f"تحذير: قيمة غير رقمية في ADMIN_IDS سيتم تجاهلها: {part}")
    return tuple(ids)


ADMIN_IDS: tuple[int, ...] = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))
VIRUSTOTAL_API_KEY: str = os.getenv("VIRUSTOTAL_API_KEY", "").strip()

# ------------------------------------------------------------------
# مجلدات العمل (تُنشأ تلقائيًا)
# ------------------------------------------------------------------
DATA_DIR: Path = BASE_DIR / "data"        # قاعدة البيانات
MEDIA_DIR: Path = BASE_DIR / "media"      # الصور التوثيقية المحفوظة
EXPORTS_DIR: Path = BASE_DIR / "exports"  # ملفات التقارير المصدرة
LOGS_DIR: Path = BASE_DIR / "logs"        # سجلات التشغيل
DB_PATH: Path = DATA_DIR / "reports.db"

# Render must use the remote database; SQLite remains the local-development default.
_IS_RENDER = any(
    os.getenv(name, "").strip()
    for name in ("RENDER_SERVICE_ID", "RENDER_SERVICE_NAME", "RENDER_EXTERNAL_URL")
)
DB_BACKEND: str = os.getenv(
    "DB_BACKEND", "turso" if _IS_RENDER else "sqlite"
).strip().lower()
if DB_BACKEND not in {"sqlite", "turso"}:
    raise ValueError("DB_BACKEND must be either 'sqlite' or 'turso'")
if _IS_RENDER and DB_BACKEND != "turso":
    raise RuntimeError("Render production requires DB_BACKEND=turso")

for _d in (DATA_DIR, MEDIA_DIR, EXPORTS_DIR, LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------------
# حدود وضوابط التشغيل (يمكن تعديلها من .env دون لمس الكود)
# ------------------------------------------------------------------
MAX_DESC_LEN: int = int(os.getenv("MAX_DESC_LEN", "1500"))          # أقصى طول للوصف
MAX_PHOTO_MB: int = int(os.getenv("MAX_PHOTO_MB", "10"))            # أقصى حجم للصورة (ميجابايت)
MAX_REPORTS_PER_DAY: int = int(os.getenv("MAX_REPORTS_PER_DAY", "10"))  # حد يومي لكل مبلّغ
# فرق التوقيت المحلي عن UTC بعدد الساعات (للعرض في التقارير والرسائل)
LOCAL_UTC_OFFSET: float = float(os.getenv("LOCAL_UTC_OFFSET", "3") or 3)

# ------------------------------------------------------------------
# طبقة الحماية: الإساءة، الإغراق، البلاغات الكيدية والتخريب
# ------------------------------------------------------------------
# حدود زمنية سريعة لكل مبلّغ (تمنع الإغراق والسبام)
MAX_REPORTS_PER_MINUTE: int = int(os.getenv("MAX_REPORTS_PER_MINUTE", "2"))
MAX_REPORTS_PER_HOUR: int = int(os.getenv("MAX_REPORTS_PER_HOUR", "5"))
MAX_REPORTS_PER_HOUR_GLOBAL: int = int(os.getenv("MAX_REPORTS_PER_HOUR_GLOBAL", "60"))
MAX_REPORTS_PER_DAY_GLOBAL: int = int(os.getenv("MAX_REPORTS_PER_DAY_GLOBAL", "300"))
FREEZE_AFTER_STRIKES: int = int(os.getenv("FREEZE_AFTER_STRIKES", "1"))
FREEZE_MINUTES_FIRST: int = int(os.getenv("FREEZE_MINUTES_FIRST", "60"))
FREEZE_MINUTES_SECOND: int = int(os.getenv("FREEZE_MINUTES_SECOND", "1440"))
FREEZE_MINUTES_REPEAT: int = int(os.getenv("FREEZE_MINUTES_REPEAT", "8640"))
AUTO_FREEZE_PAUSE_MINUTES_MIN = 5
AUTO_FREEZE_PAUSE_MINUTES_MAX = 1440
USER_LIMITS_PAUSE_MINUTES_MIN = 5
USER_LIMITS_PAUSE_MINUTES_MAX = 1440

# حدود المحتوى: الحد الأدنى من Substance في الوصف (لرفض البلاغات الفارغة)
MIN_DESC_LEN: int = int(os.getenv("MIN_DESC_LEN", "40"))
RUNTIME_SETTING_LIMITS: dict[str, tuple[int, int]] = {
    "MAX_DESC_LEN": (200, 4000),
    "MAX_PHOTO_MB": (1, 20),
    "MAX_REPORTS_PER_DAY": (1, 100),
    "MAX_REPORTS_PER_MINUTE": (1, 10),
    "MAX_REPORTS_PER_HOUR": (1, 100),
    "MAX_REPORTS_PER_HOUR_GLOBAL": (10, 10000),
    "MAX_REPORTS_PER_DAY_GLOBAL": (100, 100000),
    "FREEZE_AFTER_STRIKES": (1, 10),
    "FREEZE_MINUTES_FIRST": (1, 10080),
    "FREEZE_MINUTES_SECOND": (1, 43200),
    "FREEZE_MINUTES_REPEAT": (1, 43200),
}


def apply_runtime_settings(values: dict) -> None:
    """Apply validated, database-backed operational limits to this process."""
    for name, (minimum, maximum) in RUNTIME_SETTING_LIMITS.items():
        value = values.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            if minimum <= value <= maximum:
                globals()[name] = value
MIN_DESC_WORDS: int = int(os.getenv("MIN_DESC_WORDS", "6"))

# تشابه النص لكشف البلاغات المكررة/الكيدية (0..1)
DUPLICATE_TEXT_SIMILARITY: float = float(os.getenv("DUPLICATE_TEXT_SIMILARITY", "0.82"))

# نطاقات محجوبة (كلمة لكل نطاق) — تُرفض روابطها فورًا
BLOCKED_DOMAINS: tuple[str, ...] = tuple(
    part.strip().lower()
    for part in os.getenv("BLOCKED_DOMAINS", "").replace(";", ",").split(",")
    if part.strip()
)

# هل يسمح بتلقي البلاغات من المجموعات/القنوات؟ (لا: المحادثات الخاصة فقط)
ALLOW_GROUP_REPORTS: bool = os.getenv("ALLOW_GROUP_REPORTS", "0").strip().lower() not in {
    "0", "false", "no", "off",
}

# أقصى عدد رسائل يرسلها البوت لمستخدم واحد في الدقيقة (حماية من علامة السبام)
MAX_BOT_MESSAGES_PER_MINUTE: int = int(os.getenv("MAX_BOT_MESSAGES_PER_MINUTE", "6"))

# ------------------------------------------------------------------
# تطبيق Mini App داخل تلغرام (اختياري — يعمل فقط عند ضبط الرابط)
# ------------------------------------------------------------------
# رابط التطبيق المنشور (GitHub Pages أو Cloudflare Pages أو أي استضافة ثابتة):
MINI_APP_URL: str = os.getenv("MINI_APP_URL", "").strip()

# أنواع المخالفات المعتمدة (تظهر في التطبيق وفي التقارير)
VIOLATION_TYPES: tuple[str, ...] = (
    "تجاوز السرعة",
    "مخالفة إشارة",
    "إعاقة الطريق",
    "مخالفة زي",
    "مواد ممنوعة",
    "سلاح أو عنيف",
    "تصوير في مكان ممنوع",
    "أخرى",
)

# هل يُنشر البلاغ في القناة الرسمية تلقائيًا بعد الحفظ؟
# 0 = لا (يلزم موافقة المشرف) — وهو الخيار الآمن ضد البلاغات الكيدية.
AUTO_PUBLISH_TO_ARCHIVE: bool = os.getenv("AUTO_PUBLISH_TO_ARCHIVE", "0").strip().lower() in {
    "1", "true", "yes", "on",
}

# ------------------------------------------------------------------
# إعدادات الأرشفة السحابية (اختيارية — تعمل فقط عند ضبط القيم)
# ------------------------------------------------------------------
# معرّف القناة الرسمية الخاصة (مثل -1001234567890):
#   تُنشر فيها كل البلاغات تلقائيًا (سجل زمني موحّد)،
#   وتُحفظ فيها نسخ قاعدة البيانات (فتحمي البيانات من فقدان القرص في السحابة).
ARCHIVE_CHAT_ID: int = int(os.getenv("ARCHIVE_CHAT_ID", "0") or 0)

# هل نحفظ نسخة من الصور على القرص المحلي؟
# في الاستضافة السحابية قد لا يوجد قرص دائم، و file_id في تلغرام
# يكفي ليبقى الدليل محفوظًا إلى الأبد.
SAVE_MEDIA_LOCALLY: bool = os.getenv("SAVE_MEDIA_LOCALLY", "1").strip().lower() not in {
    "0", "false", "no", "off",
}

# رابط الخدمة على منصة الاستضافة — يُستدعى دوريًا لإبقائها نشطة
KEEPALIVE_URL: str = os.getenv("KEEPALIVE_URL", "").strip()

# عدد الثواني بين نبرات الإبقاء النشطة (300 ثانية = 5 دقائق)
# أقل من مدة نوم المنصة المجانية حتى لا تتوقف الخدمة
KEEPALIVE_INTERVAL_SEC: int = int(os.getenv("KEEPALIVE_INTERVAL_SEC", "300") or 300)

# عدد الدقائق بين نسختين احتياطيتين تلقائيتين (0 = تعطيل)
BACKUP_INTERVAL_MIN: int = int(os.getenv("BACKUP_INTERVAL_MIN", "30") or 0)

# ------------------------------------------------------------------
# النسخ الاحتياطي إلى مخزن Hugging Face Hub (الطبقة الأساسية للحماية)
# ------------------------------------------------------------------
# مستودع Dataset خاص يُرفع إليه snapshot متسق من قاعدة البيانات:
#   latest/reports.db        (النسخة الحالية — يُستبدل دائمًا)
#   daily/YYYY-MM-DD/reports.db  (لقطة واحدة لكل يوم)
# الاستعادة عند الإقلاع: فقط إذا كانت القاعدة المحلية فارغة (بلا أي بلاغ).
# التوكن يُقرأ من بيئة التشغيل (سرّ HF_TOKEN في السبيس) ولا يُكتب في الكود أبدًا.
HF_BACKUP_TOKEN: str = (
    os.getenv("HF_BACKUP_TOKEN", "") or os.getenv("HF_TOKEN", "")
).strip()
HF_BACKUP_REPO: str = os.getenv("HF_BACKUP_REPO", "azzam1992/mrw-bot-backups").strip()
