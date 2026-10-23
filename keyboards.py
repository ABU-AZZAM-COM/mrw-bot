"""
بناء لوحات الأزرار (Reply / Inline) المستخدمة في البوت.
"""
from __future__ import annotations

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from database import STATUS_NEW, STATUS_REVIEW, STATUS_APPROVED, STATUS_REJECTED
import config
import texts

# نصوص أزرار لوحة المفاتيح العادية (تُرسل كنص عادي)
CANCEL_TEXT = "❌ إلغاء"
CONFIRM_TEXT = "✅ إرسال البلاغ"

# نص زر فتح التطبيق داخل تيليغرام
APP_BUTTON_TEXT = "🚀 فتح تطبيق البلاغ"

# كود الحالة (يُستخدم في callback_data) -> الرمز التعبيري
STATUS_EMOJI = {
    "new": "🟡",
    "review": "🟠",
    "approved": "🟢",
    "rejected": "🔴",
}

# اسم الحالة بالعربية (نفس القيم المخزنة في القاعدة)
STATUS_CODE_TO_NAME = {
    "new": STATUS_NEW,
    "review": STATUS_REVIEW,
    "approved": STATUS_APPROVED,
    "rejected": STATUS_REJECTED,
}

# بادئة استدعاءات المستخدم العادي (لتمييزها عن أزرار المشرف adm:)
USR_PREFIX = "usr:"

# نص زر الحالة: رمز تعبيري + اسم الحالة
STATUS_LABELS = {
    "new": f"{STATUS_EMOJI['new']} {STATUS_NEW}",
    "review": f"{STATUS_EMOJI['review']} {STATUS_REVIEW}",
    "approved": f"{STATUS_EMOJI['approved']} {STATUS_APPROVED}",
    "rejected": f"{STATUS_EMOJI['rejected']} {STATUS_REJECTED}",
}

# نص التسمية لكل كود تصفية في لوحة المشرف
FILTER_LABELS = {
    "all": "الكل",
    **{code: STATUS_LABELS[code] for code in STATUS_LABELS},
    "الكل": "الكل",
}


def status_emoji(status_ar: str) -> str:
    """إرجاع الرمز التعبيري الخاص بحالة مخزنة بالعربية."""
    for code, name in STATUS_CODE_TO_NAME.items():
        if name == status_ar:
            return STATUS_EMOJI[code]
    return "⚪"


def _cb(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


# ------------------------------------------------------------------
# لوحات المستخدم العادي
# ------------------------------------------------------------------
def cancel_kb() -> ReplyKeyboardMarkup:
    """زر إلغاء يظهر أثناء خطوات إدخال البلاغ."""
    return ReplyKeyboardMarkup(
        [[CANCEL_TEXT]],
        resize_keyboard=True,
        input_field_placeholder="✏️ اكتب هنا… أو اختر زرًا",
    )


def confirm_kb() -> ReplyKeyboardMarkup:
    """أزرار التأكيد النهائي قبل حفظ البلاغ."""
    return ReplyKeyboardMarkup(
        [[CONFIRM_TEXT, CANCEL_TEXT]],
        resize_keyboard=True,
        input_field_placeholder="اختر زرًا 👇",
    )


def app_kb() -> ReplyKeyboardMarkup | None:
    """زر فتح تطبيق البلاغ (Mini App) — في **لوحة الرد** أسفل المحادثة.

    قاعدة تيليغرام الحاسمة (من توثيقها الرسمي):
        sendData(...) — «This method is only available for Mini Apps
        launched via a Keyboard button.»

    أي أن التطبيق يستطيع إرجاع البيانات إلى البوت **فقط** إذا فُتح من
    KeyboardButton (لوحة الرد). أما الأزرار داخل الرسائل (Inline) أو
    زر القائمة (☰) فتُفتح التطبيق لكنها لا تسمح بإرسال البيانات،
    فيصل المستخدم إلى طريق مسدود.

    لهذا السبب صار الزر هنا، مع بقاء المسار الخادمي الموثّق
    (/api/app-data) احتياطًا يعمل من أي طريقة فتح.
    """
    if not config.MINI_APP_URL:
        return None
    return ReplyKeyboardMarkup(
        [[KeyboardButton(text=APP_BUTTON_TEXT, web_app=WebAppInfo(url=config.MINI_APP_URL))]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="✏️ اكتب هنا… أو اضغط زر التطبيق 🚀",
    )


# ==================================================================
# لوحات المستخدم العادي — أزرار مضمنة (بدون أوامر كتابية في المحادثة)
# ==================================================================

def main_menu_kb() -> InlineKeyboardMarkup:
    """القائمة الرئيسية الاحترافية: كل شيء بأزرار مضمنة، لا أوامر كتابية.

    بلا زر فتح التطبيق — التطبيق متاح بأمر /app فقط عند الحاجة.
    """
    return InlineKeyboardMarkup([
        [_cb("📝 إرسال بلاغ جديد", f"{USR_PREFIX}report")],
        [
            _cb("📊 حالة بلاغي", f"{USR_PREFIX}status"),
            _cb("ℹ️ عن النظام", f"{USR_PREFIX}about"),
        ],
        [_cb("📞 تواصل مع الإدارة", f"{USR_PREFIX}contact")],
    ])


def report_type_kb() -> InlineKeyboardMarkup:
    """أزرار أنواع المخالفات للاختيار السريع (صفان في كل صف)."""
    rows = []
    for i in range(0, len(config.VIOLATION_TYPES), 2):
        row = []
        for j in range(2):
            if i + j < len(config.VIOLATION_TYPES):
                vtype = config.VIOLATION_TYPES[i + j]
                row.append(_cb(f"📌 {vtype}", f"{USR_PREFIX}type:{vtype}"))
        rows.append(row)
    rows.append([_cb("❌ إلغاء", f"{USR_PREFIX}cancel")])
    return InlineKeyboardMarkup(rows)


def confirm_report_kb() -> InlineKeyboardMarkup:
    """أزرار تأكيد الإرسال النهائي أو الإلغاء."""
    return InlineKeyboardMarkup([
        [
            _cb("✅ إرسال البلاغ", f"{USR_PREFIX}confirm"),
            _cb("❌ إلغاء", f"{USR_PREFIX}cancel"),
        ],
    ])


def back_to_menu_kb() -> InlineKeyboardMarkup:
    """زر الرجوع الوحيد الذي يبقى بعد تلاشي بقية الأزرار لإعادة إظهار القائمة."""
    return InlineKeyboardMarkup([
        [_cb("↩️ الرجوع للقائمة", f"{USR_PREFIX}menu")],
    ])


# ------------------------------------------------------------------
# لوحات المشرف
# ------------------------------------------------------------------
def status_emoji_by_code(code: str) -> str:
    return STATUS_EMOJI.get(code, "⚪")


def admin_menu_buttons(new_count: int) -> InlineKeyboardMarkup:
    """القائمة الرئيسية للوحة المشرف."""
    return InlineKeyboardMarkup(
        [
            [_cb(f"🆕 البلاغات الجديدة ({new_count})", "adm:list:new:0")],
            [_cb("🗂 كل البلاغات", "adm:list:all:0")],
            [
                _cb("📥 تصدير Excel", "adm:exp:xlsx"),
                _cb("📄 تصدير نصي", "adm:exp:txt"),
            ],
            [_cb("📊 إحصائيات", "adm:stats")],
        ]
    )


def admin_list_buttons(code: str, page: int, pages: int) -> InlineKeyboardMarkup:
    """أزرار التنقل بين صفحات قائمة البلاغات."""
    nav = []
    if page > 0:
        nav.append(_cb("◀️ السابق", f"adm:list:{code}:{page - 1}"))
    nav.append(_cb(f"📄 {page + 1}/{pages}", "adm:noop"))
    if page < pages - 1:
        nav.append(_cb("التالي ▶️", f"adm:list:{code}:{page + 1}"))
    return InlineKeyboardMarkup(
        [
            nav,
            [_cb("🏠 القائمة الرئيسية", "adm:menu")],
        ]
    )


def admin_item_buttons(items: list[dict]) -> list[list[InlineKeyboardButton]]:
    """صف زر لكل بلاغ داخل القائمة: رمز الحالة + الرقم التسلسلي."""
    rows = []
    for r in items:
        emoji = status_emoji(r["status"])
        rows.append([_cb(f"{emoji} {r['seq']}", f"adm:v:{r['id']}")])
    return rows


def filter_label(code: str) -> str:
    """اسم التصفية المعروض في رأس قائمة البلاغات."""
    return FILTER_LABELS.get(code, code)


def admin_view_buttons(rid: int) -> InlineKeyboardMarkup:
    """أزرار عرض بلاغ مفرد: تغيير الحالة + العودة للقائمة."""
    status_row = [
        _cb(STATUS_LABELS[code], f"adm:s:{rid}:{code}")
        for code in ("new", "review", "approved", "rejected")
    ]
    return InlineKeyboardMarkup(
        [
            status_row,
            [_cb("⬅️ رجوع للقائمة", "adm:back"), _cb("🏠 الرئيسية", "adm:menu")],
        ]
    )
