"""
بناء لوحات الأزرار (Reply / Inline) المستخدمة في البوت.
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

from database import STATUS_NEW, STATUS_REVIEW, STATUS_APPROVED, STATUS_REJECTED
import texts

# نصوص أزرار لوحة المفاتيح العادية (تُرسل كنص عادي)
CANCEL_TEXT = "❌ إلغاء"
CONFIRM_TEXT = "✅ إرسال البلاغ"

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
