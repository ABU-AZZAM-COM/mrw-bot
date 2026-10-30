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
from app import config
from bot import texts
from services import verified_reporters

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
    """قائمة المستخدم: إجراء البلاغ والمتابعة أولًا، والخدمات المساندة بعدها."""
    rows = [
        [_cb("📝 تقديم بلاغ جديد", f"{USR_PREFIX}report")],
        [_cb("📊 متابعة بلاغاتي", f"{USR_PREFIX}status")],
        [
            _cb("ℹ️ دليل الاستخدام", f"{USR_PREFIX}about"),
            _cb("📞 التواصل مع الإدارة", f"{USR_PREFIX}contact"),
        ],
    ]
    if verified_reporters.is_configured():
        rows.insert(
            1,
            [_cb("🪪 التسجيل كمراسل موثوق", f"{USR_PREFIX}verify")],
        )
        rows.insert(
            2,
            [_cb("🔎 حالة توثيق الحساب", f"{USR_PREFIX}verify-status")],
        )
    return InlineKeyboardMarkup(rows)


def verification_contact_kb() -> ReplyKeyboardMarkup:
    """Request the user's own Telegram contact before issuing a camera ticket."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(text=texts.SHARE_CONTACT, request_contact=True)],
            [KeyboardButton(text=CANCEL_TEXT)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def report_type_kb(
    types: Optional[list] = None,
    last_type: Optional[str] = None,
) -> InlineKeyboardMarkup:
    """أزرار أنواع المخالفات للاختيار السريع (صفان في كل صف).

    إن مُرّرت قائمة فعلية (متجرّدة من إدارة اللوحة) فتُستعمل بدل الإعداد الافتراضي.
    وعند وجود تصنيف استعمله المبلّغ سابقًا يُضاف زر اختصار له في ذيل القائمة —
    نقرة واحدة بدل البحث في القائمة من جديد.
    """
    if types is None:
        types = list(config.VIOLATION_TYPES)
    rows = []
    for i in range(0, len(types), 2):
        row = []
        for j in range(2):
            if i + j < len(types):
                vtype = types[i + j]
                row.append(_cb(f"📌 {vtype}", f"{USR_PREFIX}type:{vtype}"))
        rows.append(row)
    if last_type and last_type in types:
        rows.append([
            _cb(f"🔁 نفس تصنيفي السابق: {last_type}", f"{USR_PREFIX}type:{last_type}"),
        ])
    rows.append([_cb("❌ إلغاء", f"{USR_PREFIX}cancel")])
    return InlineKeyboardMarkup(rows)


REPORT_CATEGORIES = {
    "conduct": "🚨 تجاوزات وسلوك فردي",
    "administrative": "⚖️ مخالفات إدارية ومالية",
    "social": "📱 مخالفات النشر والتواصل الاجتماعي",
    "field": "📍 بلاغ ميداني عاجل / أخرى",
}

SOCIAL_PLATFORM_LABELS = {
    "facebook": "فيسبوك",
    "instagram_tiktok": "إنستغرام / تيك توك",
    "whatsapp": "واتساب",
    "telegram": "تيليجرام",
    "other": "منصة أخرى",
}


def report_category_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [_cb(label, f"{USR_PREFIX}category:{key}")]
            for key, label in REPORT_CATEGORIES.items()
        ]
        + [[_cb("🏠 القائمة الرئيسية", f"{USR_PREFIX}menu")]]
    )


def social_platform_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [_cb(label, f"{USR_PREFIX}platform:{key}")]
            for key, label in SOCIAL_PLATFORM_LABELS.items()
        ]
        + [
            [
                _cb("⬅️ رجوع للقسم", f"{USR_PREFIX}back:category"),
                _cb("❌ إلغاء", f"{USR_PREFIX}cancel"),
            ]
        ]
    )


def optional_step_kb(step: str) -> InlineKeyboardMarkup:
    labels = {
        "photo": "⏭️ تخطي وإكمال البلاغ",
        "link": "⏭️ لا أملك رابطًا",
        "location": "⏭️ تخطي الموقع",
        "contact": "⏭️ تخطي بيانات التواصل",
    }
    if step not in labels:
        raise ValueError(f"Unknown optional report step: {step}")
    previous_step = {
        "photo": "description",
        "link": "photo",
        "location": "photo",
        "contact": "photo",
    }[step]
    return InlineKeyboardMarkup([
        [_cb(labels[step], f"{USR_PREFIX}skip:{step}")],
        [
            _cb("⬅️ رجوع", f"{USR_PREFIX}back:{previous_step}"),
            _cb("❌ إلغاء", f"{USR_PREFIX}cancel"),
        ],
    ])


def contact_request_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(text=texts.SHARE_CONTACT, request_contact=True)],
            [KeyboardButton(text="⏭️ تخطي")],
            [KeyboardButton(text="↩️ رجوع")],
            [KeyboardButton(text=CANCEL_TEXT)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def location_request_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(text="📍 مشاركة الموقع", request_location=True)],
            [KeyboardButton(text="⏭️ تخطي")],
            [KeyboardButton(text="↩️ رجوع")],
            [KeyboardButton(text=CANCEL_TEXT)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def cancel_inline_kb(back_to: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    if back_to:
        rows.append([_cb("⬅️ رجوع", f"{USR_PREFIX}back:{back_to}")])
    rows.append([_cb("❌ إلغاء", f"{USR_PREFIX}cancel")])
    return InlineKeyboardMarkup(rows)


def confirm_report_kb() -> InlineKeyboardMarkup:
    """أزرار تأكيد الإرسال النهائي أو الإلغاء."""
    return InlineKeyboardMarkup([
        [
            _cb("✅ إرسال البلاغ", f"{USR_PREFIX}confirm"),
            _cb("❌ إلغاء", f"{USR_PREFIX}cancel"),
        ],
        [_cb("⬅️ مراجعة البيانات", f"{USR_PREFIX}back:contact")],
    ])


def saved_receipt_kb() -> InlineKeyboardMarkup:
    """Keep the saved-report receipt intact when the user returns to the menu."""
    return InlineKeyboardMarkup([
        [_cb("🏠 القائمة الرئيسية", f"{USR_PREFIX}home")],
    ])


def back_to_menu_kb() -> InlineKeyboardMarkup:
    """زر الرجوع الوحيد الذي يبقى بعد تلاشي بقية الأزرار لإعادة إظهار القائمة."""
    return InlineKeyboardMarkup([
        [_cb("↩️ الرجوع للقائمة", f"{USR_PREFIX}menu")],
    ])


def my_reports_kb(items: list[dict]) -> InlineKeyboardMarkup:
    """شاشة «بلاغاتي»: زر لكل بطاقة بلاغ (رمز الحالة + رقمه) ثم العودة للقائمة."""
    rows = [
        [_cb(f"{status_emoji(r['status'])} {r['seq']}", f"{USR_PREFIX}r:{r['id']}")]
        for r in items
    ]
    rows.append([_cb("🏠 العودة إلى القائمة", f"{USR_PREFIX}menu")])
    return InlineKeyboardMarkup(rows)


def report_open_kb(report_id: int, followup_requested: bool = False) -> InlineKeyboardMarkup:
    """أزرار بطاقة بلاغ مفرد في «بلاغاتي»: عودة للقائمة أو للقائمة الرئيسية."""
    rows = []
    if followup_requested:
        rows.append([_cb("📩 إرسال المعلومات المطلوبة", f"{USR_PREFIX}followup:{report_id}")])
    rows.extend([
        [_cb("⬅️ عودة إلى بلاغاتي", f"{USR_PREFIX}status")],
        [_cb("🏠 القائمة الرئيسية", f"{USR_PREFIX}menu")],
    ])
    return InlineKeyboardMarkup(rows)


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
        for code in ("new", "review", "approved")
    ]
    return InlineKeyboardMarkup(
        [
            status_row,
            [_cb("❌ رفض بسبب", f"adm:rj:{rid}")],
            [_cb("⬅️ رجوع للقائمة", "adm:back"), _cb("🏠 الرئيسية", "adm:menu")],
        ]
    )


def admin_notify_buttons(rid: int) -> InlineKeyboardMarkup:
    """أزرار القرار السريع على إشعار البلاغ الجديد لدى المشرف.

    كل زر ينفَّذ آلية اللوحة نفسها (تغيير حالة موثّق بسجل التدقيق وإبلاغ المبلّغ):
      ✅ اعتماد فوري / 🟠 إحالة للمراجعة → adm:s (موجود)؛
      ❌ رفض بسبب → adm:rj (يطلب كتابة السبب أولًا)؛
      ℹ️ التفاصيل → adm:v (افتح عرض بلاغ كامل).
    """
    return InlineKeyboardMarkup([
        [
            _cb("✅ اعتماد فوري", f"adm:s:{rid}:approved"),
            _cb("🟠 إحالة للمراجعة", f"adm:s:{rid}:review"),
        ],
        [
            _cb("❌ رفض بسبب", f"adm:rj:{rid}"),
            _cb("ℹ️ التفاصيل", f"adm:v:{rid}"),
        ],
    ])


def admin_reject_cancel_kb(rid: int) -> InlineKeyboardMarkup:
    """زر التراجع عن كتابة سبب الرفض قبل إرساله."""
    return InlineKeyboardMarkup([[_cb("🚫 إلغاء الرفض", f"adm:rc:{rid}")]])
