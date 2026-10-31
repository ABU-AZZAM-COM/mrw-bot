"""
بناء لوحات الأزرار (Reply / Inline) المستخدمة في البوت.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from telegram import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from database import (
    STATUS_AWAITING_INFO,
    STATUS_CLOSED_NO_RESPONSE,
    STATUS_NEW,
    STATUS_REVIEW,
    STATUS_APPROVED,
    STATUS_REJECTED,
)
from app import config
from bot import control, texts
from services import verified_reporters

# نصوص أزرار لوحة المفاتيح العادية (تُرسل كنص عادي)
CANCEL_TEXT = control.CANCEL_LABEL
CONFIRM_TEXT = control.CONFIRM_LABEL

# نص زر فتح التطبيق داخل تيليغرام
APP_BUTTON_TEXT = "🚀 فتح تطبيق البلاغ"

# كود الحالة (يُستخدم في callback_data) -> الرمز التعبيري
STATUS_EMOJI = {
    "new": "🟡",
    "review": "🟠",
    "approved": "🟢",
    "rejected": "🔴",
    "awaiting_info": "🟠",
    "closed_no_response": "⚫",
}

# اسم الحالة بالعربية (نفس القيم المخزنة في القاعدة)
STATUS_CODE_TO_NAME = {
    "new": STATUS_NEW,
    "review": STATUS_REVIEW,
    "approved": STATUS_APPROVED,
    "rejected": STATUS_REJECTED,
    "awaiting_info": STATUS_AWAITING_INFO,
    "closed_no_response": STATUS_CLOSED_NO_RESPONSE,
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
    """زر إلغاء يظهر أثناء خطوات إدخال البلاغ (قابل للتعديل من اللوحة)."""
    markup = control.build_reply_keyboard("cancel")
    if markup is not None:
        return markup
    return ReplyKeyboardMarkup(
        [[CANCEL_TEXT]],
        resize_keyboard=True,
        input_field_placeholder="✏️ اكتب هنا… أو اختر زرًا",
    )


def confirm_kb() -> ReplyKeyboardMarkup:
    """أزرار التأكيد النهائي قبل حفظ البلاغ (قابلة للتعديل من اللوحة)."""
    markup = control.build_reply_keyboard("confirm")
    if markup is not None:
        return markup
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
    if not control.mini_app_url():
        return None
    return control.build_reply_keyboard("app")


# ==================================================================
# لوحات المستخدم العادي — أزرار مضمنة (بدون أوامر كتابية في المحادثة)
# ==================================================================

# ------------------------------------------------------------------
# تخطيط قائمة المستخدم الرئيسية — قابل للتعديل من لوحة الإدارة
# ------------------------------------------------------------------
# يُحفظ التخطيط في جدول الحالة داخل القاعدة تحت هذا المفتاح (JSON)،
# ويُقرأ عند إقلاع البوت وعند كل حفظ من اللوحة، فيتبدل البوت فورًا
# دون إعادة نشر. القيم المسموحة للإجراءات هي نفسها أزرار usr: الحالية.
USER_MENU_STATE_KEY = "user_menu_layout"

USER_MENU_ACTIONS: dict[str, str] = {
    "report": "📝 تقديم بلاغ جديد",
    "status": "📊 متابعة بلاغاتي",
    "about": "ℹ️ دليل الاستخدام",
    "contact": "📞 التواصل مع الإدارة",
    "privacy": "🔒 سياسة الخصوصية",
    "verify": "🪪 التسجيل كمراسل موثوق",
    "verify-status": "🔎 حالة توثيق الحساب",
    "menu": "🏠 القائمة الرئيسية",
    "home": "🏠 العودة للقائمة",
    "cancel": "❌ إلغاء",
}

# الإجراءات التي تحتاج تفعيل تشفير التوثيق قبل عرضها
_VERIFICATION_MENU_ACTIONS = {"verify", "verify-status"}

MAX_USER_MENU_ROWS = 8
MAX_USER_MENU_ROW_BUTTONS = 3
MAX_USER_MENU_BUTTONS = 16
MAX_USER_MENU_LABEL = 40
MAX_USER_MENU_URL = 512
MAX_USER_MENU_COPY = 256

MENU_BUTTON_TYPES = ("callback", "url", "web_app", "copy", "inline")
MENU_VISIBILITY = ("all", "admins", "non_admins")

_MENU_URL_RE = re.compile(r"^(https://|http://|tg://|mailto:)[^\s]+$", re.IGNORECASE)

# آخر تخطيط مُحمَّل — None يعني استخدام الافتراضي المضمّن في الكود.
_user_menu_layout: Optional[list[list[dict[str, str]]]] = None


def default_user_menu_layout() -> list[list[dict[str, str]]]:
    """التخطيط الافتراضي لقائمة المستخدم (نفس ترتيب الأزرار الحالي)."""
    rows: list[list[dict[str, str]]] = [
        [{"label": USER_MENU_ACTIONS["report"], "action": "report"}],
    ]
    if verified_reporters.is_configured():
        rows.append([{"label": USER_MENU_ACTIONS["verify"], "action": "verify"}])
        rows.append([
            {
                "label": USER_MENU_ACTIONS["verify-status"],
                "action": "verify-status",
            }
        ])
    rows.extend([
        [{"label": USER_MENU_ACTIONS["status"], "action": "status"}],
        [
            {"label": USER_MENU_ACTIONS["about"], "action": "about"},
            {"label": USER_MENU_ACTIONS["contact"], "action": "contact"},
        ],
        [{"label": USER_MENU_ACTIONS["privacy"], "action": "privacy"}],
    ])
    return rows


def _clean_menu_button(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    """يتحقق من زر واحد في القائمة ويعيد نسخة نظيفة أو None."""
    label = item.get("label")
    if not isinstance(label, str):
        return None
    label = label.strip()
    if not label or len(label) > MAX_USER_MENU_LABEL:
        return None

    kind = item.get("type", "callback")
    if not isinstance(kind, str) or kind not in MENU_BUTTON_TYPES:
        return None

    clean: dict[str, Any] = {"label": label, "type": kind}

    if kind == "callback":
        action = item.get("action")
        if not isinstance(action, str) or action.strip() not in USER_MENU_ACTIONS:
            return None
        clean["action"] = action.strip()
    else:
        clean["action"] = ""

    style = item.get("style")
    if style is not None and style != "":
        if not isinstance(style, str) or style not in control.STYLE_VALUES:
            return None
        clean["style"] = style

    icon = item.get("icon")
    if icon is not None and icon != "":
        if not isinstance(icon, str) or not icon.strip().isdigit():
            return None
        clean["icon"] = icon.strip()

    disabled = item.get("disabled", False)
    if not isinstance(disabled, bool):
        return None
    clean["disabled"] = disabled

    visibility = item.get("visibility", "all")
    if not isinstance(visibility, str) or visibility not in MENU_VISIBILITY:
        return None
    if visibility != "all":
        clean["visibility"] = visibility

    if kind in ("url", "web_app"):
        url = item.get("url")
        if not isinstance(url, str):
            return None
        url = url.strip()
        if not url or len(url) > MAX_USER_MENU_URL:
            return None
        if kind == "web_app" and not url.lower().startswith("https://"):
            return None
        if not _MENU_URL_RE.match(url):
            return None
        clean["url"] = url
    elif kind == "copy":
        copy_text = item.get("copy_text")
        if not isinstance(copy_text, str):
            return None
        copy_text = copy_text.strip()
        if not copy_text or len(copy_text) > MAX_USER_MENU_COPY:
            return None
        clean["copy_text"] = copy_text
    elif kind == "inline":
        query = item.get("inline_query", "")
        if not isinstance(query, str) or len(query) > MAX_USER_MENU_COPY:
            return None
        clean["inline_query"] = query.strip()

    return clean


def normalize_user_menu_layout(
    raw: Any,
) -> Optional[list[list[dict[str, Any]]]]:
    """يتحقق من تخطيط قائمة المستخدم ويعيده نظيفًا، أو None إن كان غير صالح.

    يقبل قائمة جاهزة أو نص JSON مخزّنًا في القاعدة، ويقبل التخطيطات القديمة
    التي تحمل ``label`` و``action`` فقط (تُفسَّر كأزرار callback).
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            raw = json.loads(text)
        except ValueError:
            return None
    if not isinstance(raw, list) or not raw:
        return None
    if len(raw) > MAX_USER_MENU_ROWS:
        return None
    rows: list[list[dict[str, Any]]] = []
    seen_actions: set[str] = set()
    total = 0
    for row in raw:
        if not isinstance(row, list) or not row:
            return None
        if len(row) > MAX_USER_MENU_ROW_BUTTONS:
            return None
        clean_row: list[dict[str, Any]] = []
        for item in row:
            if not isinstance(item, dict):
                return None
            clean = _clean_menu_button(item)
            if clean is None:
                return None
            if clean["type"] == "callback":
                action = clean["action"]
                if action in seen_actions:
                    return None
                seen_actions.add(action)
            clean_row.append(clean)
            total += 1
        rows.append(clean_row)
    if not 1 <= total <= MAX_USER_MENU_BUTTONS:
        return None
    return rows


def set_user_menu_layout(raw: Any) -> Optional[list[list[dict[str, Any]]]]:
    """يثبّت التخطيط في الذاكرة. None أو تخطيط تالف = العودة للافتراضي."""
    global _user_menu_layout
    _user_menu_layout = normalize_user_menu_layout(raw)
    return _user_menu_layout


def current_user_menu_layout() -> list[list[dict[str, Any]]]:
    """التخطيط الفعلي المعروض للمستخدم الآن."""
    if _user_menu_layout is not None:
        return [list(row) for row in _user_menu_layout]
    return default_user_menu_layout()


def _menu_inline_button(item: dict[str, Any]) -> Optional[InlineKeyboardButton]:
    kind = item.get("type", "callback")
    kwargs: dict[str, Any] = {"text": item["label"]}
    if item.get("style"):
        kwargs["style"] = item["style"]
    if item.get("icon"):
        kwargs["icon_custom_emoji_id"] = item["icon"]
    if item.get("disabled"):
        kwargs["api_kwargs"] = {"disabled": {"is_disabled": True}}

    if kind == "callback" or not kind:
        return InlineKeyboardButton(
            callback_data=f"{USR_PREFIX}{item['action']}", **kwargs
        )
    if kind == "url":
        return InlineKeyboardButton(url=item["url"], **kwargs)
    if kind == "web_app":
        return InlineKeyboardButton(web_app=WebAppInfo(url=item["url"]), **kwargs)
    if kind == "copy":
        return InlineKeyboardButton(
            copy_text=CopyTextButton(text=item["copy_text"]), **kwargs
        )
    if kind == "inline":
        return InlineKeyboardButton(
            switch_inline_query_current_chat=item.get("inline_query", ""), **kwargs
        )
    return None


def menu_button_visible(item: dict[str, Any], viewer_id: Optional[int]) -> bool:
    visibility = item.get("visibility", "all")
    if visibility == "all" or viewer_id is None:
        return True
    is_admin = int(viewer_id) in getattr(config, "ADMIN_IDS", set())
    return is_admin if visibility == "admins" else not is_admin


def main_menu_kb(viewer_id: Optional[int] = None) -> InlineKeyboardMarkup:
    """قائمة المستخدم: إجراء البلاغ والمتابعة أولًا، والخدمات المساندة بعدها.

    تُبنى من التخطيط المحفوظ في اللوحة إن وُجد، وإلا من الافتراضي.
    أزرار التوثيق تُخفى تلقائيًا إذا كان التشفير غير مهيأ.
    """
    verification_ready = verified_reporters.is_configured()
    rows: list[list[InlineKeyboardButton]] = []
    for row in current_user_menu_layout():
        buttons: list[InlineKeyboardButton] = []
        for item in row:
            if (
                item.get("type", "callback") == "callback"
                and not verification_ready
                and item.get("action") in _VERIFICATION_MENU_ACTIONS
            ):
                continue
            if not menu_button_visible(item, viewer_id):
                continue
            button = _menu_inline_button(item)
            if button is not None:
                buttons.append(button)
        if buttons:
            rows.append(buttons)
    if not rows:
        rows = [[_cb(texts.KEYBOARD_LABELS["report_start"], f"{USR_PREFIX}report")]]
    return InlineKeyboardMarkup(rows)


def verification_contact_kb() -> ReplyKeyboardMarkup:
    """Request the user's own Telegram contact before issuing a camera ticket."""
    markup = control.build_reply_keyboard("verification_contact")
    if markup is not None:
        return markup
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(text=texts.SHARE_CONTACT, request_contact=True)],
            [KeyboardButton(text=CANCEL_TEXT)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def verification_consent_kb(
    privacy_url: str | None = None,
    consent_version: str = "reporter-consent-v1",
) -> InlineKeyboardMarkup:
    rows = [
        [_cb(texts.KEYBOARD_LABELS["consent_continue"], f"{USR_PREFIX}verify-consent:{consent_version}")],
        [_cb(texts.KEYBOARD_LABELS["cancel_plain"], f"{USR_PREFIX}cancel")],
    ]
    if privacy_url and privacy_url.startswith("https://"):
        rows.insert(
            0,
            [InlineKeyboardButton(texts.KEYBOARD_LABELS["privacy_policy"], url=privacy_url)],
        )
    return InlineKeyboardMarkup(rows)


def privacy_policy_kb(privacy_url: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    if privacy_url and privacy_url.startswith("https://"):
        rows.append([InlineKeyboardButton(texts.KEYBOARD_LABELS["privacy_read"], url=privacy_url)])
    rows.append([_cb(texts.KEYBOARD_LABELS["return_menu"], f"{USR_PREFIX}menu")])
    return InlineKeyboardMarkup(rows)


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
                row.append(_cb(
                    texts.KEYBOARD_LABELS["report_type_prefix"].format(type=vtype),
                    f"{USR_PREFIX}type:{vtype}",
                ))
        rows.append(row)
    if last_type and last_type in types:
        rows.append([
            _cb(
                texts.KEYBOARD_LABELS["last_report_type"].format(type=last_type),
                f"{USR_PREFIX}type:{last_type}",
            ),
        ])
    rows.append([_cb(texts.KEYBOARD_LABELS["cancel"], f"{USR_PREFIX}cancel")])
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
        + [[_cb(texts.KEYBOARD_LABELS["main_menu"], f"{USR_PREFIX}menu")]]
    )


def social_platform_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [_cb(label, f"{USR_PREFIX}platform:{key}")]
            for key, label in SOCIAL_PLATFORM_LABELS.items()
        ]
        + [
            [
                _cb(texts.KEYBOARD_LABELS["back_to_category"], f"{USR_PREFIX}back:category"),
                _cb(texts.KEYBOARD_LABELS["cancel"], f"{USR_PREFIX}cancel"),
            ]
        ]
    )


def optional_step_kb(step: str) -> InlineKeyboardMarkup:
    labels = {
        "photo": texts.KEYBOARD_LABELS["skip_photo"],
        "link": texts.KEYBOARD_LABELS["skip_link"],
        "location": texts.KEYBOARD_LABELS["skip_location"],
        "contact": texts.KEYBOARD_LABELS["skip_contact"],
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
            _cb(texts.KEYBOARD_LABELS["back"], f"{USR_PREFIX}back:{previous_step}"),
            _cb(texts.KEYBOARD_LABELS["cancel"], f"{USR_PREFIX}cancel"),
        ],
    ])


def contact_request_kb() -> ReplyKeyboardMarkup:
    markup = control.build_reply_keyboard("contact")
    if markup is not None:
        return markup
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(text=texts.SHARE_CONTACT, request_contact=True)],
            [KeyboardButton(text=control.SKIP_LABEL)],
            [KeyboardButton(text=control.BACK_LABEL)],
            [KeyboardButton(text=CANCEL_TEXT)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def location_request_kb() -> ReplyKeyboardMarkup:
    markup = control.build_reply_keyboard("location")
    if markup is not None:
        return markup
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(text=control.LOCATION_LABEL, request_location=True)],
            [KeyboardButton(text=control.SKIP_LABEL)],
            [KeyboardButton(text=control.BACK_LABEL)],
            [KeyboardButton(text=CANCEL_TEXT)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def cancel_inline_kb(back_to: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    if back_to:
        rows.append([_cb(texts.KEYBOARD_LABELS["back"], f"{USR_PREFIX}back:{back_to}")])
    rows.append([_cb(texts.KEYBOARD_LABELS["cancel"], f"{USR_PREFIX}cancel")])
    return InlineKeyboardMarkup(rows)


def confirm_report_kb() -> InlineKeyboardMarkup:
    """أزرار تأكيد الإرسال النهائي أو الإلغاء."""
    return InlineKeyboardMarkup([
        [
            _cb(texts.KEYBOARD_LABELS["confirm_report"], f"{USR_PREFIX}confirm"),
            _cb(texts.KEYBOARD_LABELS["cancel"], f"{USR_PREFIX}cancel"),
        ],
        [_cb(texts.KEYBOARD_LABELS["review_report"], f"{USR_PREFIX}back:contact")],
    ])


def saved_receipt_kb() -> InlineKeyboardMarkup:
    """Keep the saved-report receipt intact when the user returns to the menu."""
    return InlineKeyboardMarkup([
        [_cb(texts.KEYBOARD_LABELS["home"], f"{USR_PREFIX}home")],
    ])


def back_to_menu_kb() -> InlineKeyboardMarkup:
    """زر الرجوع الوحيد الذي يبقى بعد تلاشي بقية الأزرار لإعادة إظهار القائمة."""
    return InlineKeyboardMarkup([
        [_cb(texts.KEYBOARD_LABELS["back_menu"], f"{USR_PREFIX}menu")],
    ])


def my_reports_kb(items: list[dict]) -> InlineKeyboardMarkup:
    """شاشة «بلاغاتي»: زر لكل بطاقة بلاغ (رمز الحالة + رقمه) ثم العودة للقائمة."""
    rows = [
        [_cb(f"{status_emoji(r['status'])} {r['seq']}", f"{USR_PREFIX}r:{r['id']}")]
        for r in items
    ]
    rows.append([_cb(texts.KEYBOARD_LABELS["main_menu"], f"{USR_PREFIX}menu")])
    return InlineKeyboardMarkup(rows)


def report_open_kb(
    report_id: int,
    followup_requested: bool = False,
    can_appeal: bool = False,
) -> InlineKeyboardMarkup:
    """أزرار بطاقة بلاغ مفرد في «بلاغاتي»: عودة للقائمة أو للقائمة الرئيسية."""
    rows = []
    if followup_requested:
        rows.append([_cb(texts.KEYBOARD_LABELS["send_followup"], f"{USR_PREFIX}followup:{report_id}")])
    if can_appeal:
        rows.append([_cb(texts.KEYBOARD_LABELS["send_appeal"], f"{USR_PREFIX}appeal:{report_id}")])
    rows.extend([
        [_cb(texts.KEYBOARD_LABELS["return_to_reports"], f"{USR_PREFIX}status")],
        [_cb(texts.KEYBOARD_LABELS["main_menu"], f"{USR_PREFIX}menu")],
    ])
    return InlineKeyboardMarkup(rows)


def report_followup_reply_kb(report_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[_cb(texts.KEYBOARD_LABELS["send_followup"], f"{USR_PREFIX}followup:{report_id}")]]
    )


def report_appeal_kb(report_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[_cb(texts.KEYBOARD_LABELS["submit_appeal"], f"{USR_PREFIX}appeal:{report_id}")]]
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
            [_cb(
                texts.ADMIN_KEYBOARD_LABELS["new_reports"].format(count=new_count),
                "adm:list:new:0",
            )],
            [_cb(texts.ADMIN_KEYBOARD_LABELS["all_reports"], "adm:list:all:0")],
            [
                _cb(texts.ADMIN_KEYBOARD_LABELS["export_excel"], "adm:exp:xlsx"),
                _cb(texts.ADMIN_KEYBOARD_LABELS["export_text"], "adm:exp:txt"),
            ],
            [_cb(texts.ADMIN_KEYBOARD_LABELS["stats"], "adm:stats")],
        ]
    )


def admin_list_buttons(code: str, page: int, pages: int) -> InlineKeyboardMarkup:
    """أزرار التنقل بين صفحات قائمة البلاغات."""
    nav = []
    if page > 0:
        nav.append(_cb(texts.ADMIN_KEYBOARD_LABELS["previous"], f"adm:list:{code}:{page - 1}"))
    nav.append(_cb(
        texts.ADMIN_KEYBOARD_LABELS["page"].format(
            current=page + 1, total=pages
        ),
        "adm:noop",
    ))
    if page < pages - 1:
        nav.append(_cb(texts.ADMIN_KEYBOARD_LABELS["next"], f"adm:list:{code}:{page + 1}"))
    return InlineKeyboardMarkup(
        [
            nav,
            [_cb(texts.ADMIN_KEYBOARD_LABELS["home"], "adm:menu")],
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
            [_cb(texts.ADMIN_KEYBOARD_LABELS["reject_reason"], f"adm:rj:{rid}")],
            [
                _cb(texts.ADMIN_KEYBOARD_LABELS["back_to_list"], "adm:back"),
                _cb(texts.ADMIN_KEYBOARD_LABELS["main"], "adm:menu"),
            ],
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
            _cb(texts.ADMIN_KEYBOARD_LABELS["approve_now"], f"adm:s:{rid}:approved"),
            _cb(texts.ADMIN_KEYBOARD_LABELS["send_review"], f"adm:s:{rid}:review"),
        ],
        [
            _cb(texts.ADMIN_KEYBOARD_LABELS["reject_reason"], f"adm:rj:{rid}"),
            _cb(texts.ADMIN_KEYBOARD_LABELS["details"], f"adm:v:{rid}"),
        ],
    ])


def admin_reject_cancel_kb(rid: int) -> InlineKeyboardMarkup:
    """زر التراجع عن كتابة سبب الرفض قبل إرساله."""
    return InlineKeyboardMarkup([[
        _cb(texts.ADMIN_KEYBOARD_LABELS["cancel_rejection"], f"adm:rc:{rid}")
    ]])
