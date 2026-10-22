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

# ------------------------------------------------------------------
# مجلدات العمل (تُنشأ تلقائيًا)
# ------------------------------------------------------------------
DATA_DIR: Path = BASE_DIR / "data"        # قاعدة البيانات
MEDIA_DIR: Path = BASE_DIR / "media"      # الصور التوثيقية المحفوظة
EXPORTS_DIR: Path = BASE_DIR / "exports"  # ملفات التقارير المصدرة
LOGS_DIR: Path = BASE_DIR / "logs"        # سجلات التشغيل
DB_PATH: Path = DATA_DIR / "reports.db"

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
