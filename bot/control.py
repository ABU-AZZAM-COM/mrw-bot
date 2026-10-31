"""مركز التحكم في البوت — الإعدادات التي تُدار من لوحة الإدارة.

هذا الملف مستقل عن ``bot.keyboards`` حتى لا تحدث دورة استيراد:
- ``bot.keyboards`` يستورد منه لوحات الرد والنصوص الدلالية.
- ``api.admin_api`` يستخدمه للحفظ والتطبيق على تيليجرام.
- ``app.main`` يقرأ الإعدادات عند الإقلاع ويطبّقها.

كل الإعدادات تُخزَّن في ``system_state`` تحت مفاتيح ``bot_*`` وتبقى
متوافقة مع القواعد والنسخ القديمة (أي مفتاح غائب = الافتراضي).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from telegram import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeChatAdministrators,
    BotCommandScopeDefault,
    ChatAdministratorRights,
    KeyboardButton,
    MenuButtonCommands,
    MenuButtonDefault,
    MenuButtonWebApp,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from app import config

logger = logging.getLogger(__name__)

REPLY_STATE_KEY = "bot_reply_keyboards"
COMMANDS_STATE_KEY = "bot_commands"
MENU_BUTTON_STATE_KEY = "bot_menu_button"
SETTINGS_STATE_KEY = "bot_settings"

STYLE_VALUES = ("primary", "success", "danger")
BUTTON_TYPES = ("callback", "url", "web_app", "copy", "inline")
REPLY_BUTTON_TYPES = (
    "text",
    "cancel",
    "confirm",
    "contact",
    "location",
    "skip",
    "back",
    "web_app",
)

# نصوص افتراضية مطابقة لما كان مكتوبًا في keyboards.py
CANCEL_LABEL = "❌ إلغاء"
CONFIRM_LABEL = "✅ إرسال البلاغ"
APP_LABEL = "🚀 فتح تطبيق البلاغ"
SKIP_LABEL = "⏭️ تخطي"
BACK_LABEL = "↩️ رجوع"
LOCATION_LABEL = "📍 مشاركة الموقع"
SHARE_CONTACT_LABEL = "📞 مشاركة رقم هاتفي"

MAX_REPLY_ROWS = 6
MAX_REPLY_ROW_BUTTONS = 4
MAX_REPLY_LABEL = 64
MAX_COMMANDS = 100
MAX_SCOPES = 50
MAX_MENU_TEXT = 64
MAX_URL = 512
MAX_DEEP_LINK_PAYLOAD = 64

_REPLY_IDS = (
    "cancel",
    "confirm",
    "app",
    "verification_contact",
    "contact",
    "location",
)

_REPLY_ID_LABELS = {
    "cancel": "لوحة الإلغاء (أثناء إدخال البلاغ)",
    "confirm": "لوحة تأكيد الإرسال",
    "app": "لوحة فتح تطبيق البلاغ",
    "verification_contact": "لوحة توثيق المراسل",
    "contact": "لوحة التواصل مع الإدارة",
    "location": "لوحة طلب الموقع",
}

_SCOPE_LABELS = {
    "default": "الافتراضي (كل المحادثات)",
    "all_private_chats": "كل المحادثات الخاصة",
    "all_group_chats": "كل المجموعات",
    "all_chat_administrators": "كل مشرفي المحادثات",
    "chat": "محادثة محددة",
    "chat_administrators": "مشرفو محادثة محددة",
}

_COMMAND_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_URL_RE = re.compile(r"^(https://|http://|tg://|mailto:)[^\s]+$", re.IGNORECASE)
_LANG_RE = re.compile(r"^[a-zA-Z]{2}(?:[-_][a-zA-Z0-9]{2,8})?$")

# ----------------------------------------------------------------------
# الحالة داخل الذاكرة (تُحمَّل عند الإقلاع وتُحدَّث فور الحفظ)
# ----------------------------------------------------------------------
_reply_keyboards: dict[str, dict[str, Any]] = {}
_commands_config: dict[str, Any] = {}
_menu_button_config: dict[str, Any] = {}
_settings: dict[str, Any] = {}


def _loads(raw: Any) -> Optional[Any]:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None
    return None


# ----------------------------------------------------------------------
# لوحات الرد (Reply Keyboards)
# ----------------------------------------------------------------------
def default_reply_keyboards() -> dict[str, dict[str, Any]]:
    app_url = config.MINI_APP_URL or ""

    def row(*buttons: dict[str, Any]) -> list[dict[str, Any]]:
        return list(buttons)

    def btn(text: str, kind: str = "text") -> dict[str, Any]:
        return {"text": text, "type": kind}

    return {
        "cancel": {
            "rows": [row(btn(CANCEL_LABEL, "cancel"))],
            "placeholder": "✏️ اكتب هنا… أو اختر زرًا",
            "persistent": False,
        },
        "confirm": {
            "rows": [row(btn(CONFIRM_LABEL, "confirm"), btn(CANCEL_LABEL, "cancel"))],
            "placeholder": "اختر زرًا 👇",
            "persistent": False,
        },
        "app": {
            "rows": [row(btn(APP_LABEL, "web_app"))],
            "placeholder": "✏️ اكتب هنا… أو اضغط زر التطبيق 🚀",
            "persistent": True,
            "requires_url": app_url,
        },
        "verification_contact": {
            "rows": [
                row(btn(SHARE_CONTACT_LABEL, "contact")),
                row(btn(CANCEL_LABEL, "cancel")),
            ],
            "placeholder": "📞 شارك رقمك أو اختر إلغاء",
            "persistent": False,
        },
        "contact": {
            "rows": [
                row(btn(SHARE_CONTACT_LABEL, "contact")),
                row(btn(SKIP_LABEL, "skip")),
                row(btn(BACK_LABEL, "back")),
                row(btn(CANCEL_LABEL, "cancel")),
            ],
            "placeholder": "اختر زرًا 👇",
            "persistent": False,
        },
        "location": {
            "rows": [
                row(btn(LOCATION_LABEL, "location")),
                row(btn(SKIP_LABEL, "skip")),
                row(btn(BACK_LABEL, "back")),
                row(btn(CANCEL_LABEL, "cancel")),
            ],
            "placeholder": "اختر زرًا 👇",
            "persistent": False,
        },
    }


def _clean_reply_button(item: Any) -> Optional[dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    text = item.get("text")
    kind = item.get("type", "text")
    if not isinstance(text, str) or not isinstance(kind, str):
        return None
    text = text.strip()
    kind = kind.strip()
    if not text or len(text) > MAX_REPLY_LABEL:
        return None
    if kind not in REPLY_BUTTON_TYPES:
        return None
    clean: dict[str, Any] = {"text": text, "type": kind}
    style = item.get("style")
    if isinstance(style, str) and style in STYLE_VALUES:
        clean["style"] = style
    icon = item.get("icon")
    if isinstance(icon, str) and icon.strip().isdigit():
        clean["icon"] = icon.strip()
    return clean


def normalize_reply_keyboards(raw: Any) -> Optional[dict[str, dict[str, Any]]]:
    data = _loads(raw)
    if data is None:
        return None
    if not isinstance(data, dict):
        return None
    defaults = default_reply_keyboards()
    result: dict[str, dict[str, Any]] = {}
    for keyboard_id in _REPLY_IDS:
        entry = data.get(keyboard_id, defaults[keyboard_id])
        if not isinstance(entry, dict):
            return None
        rows_raw = entry.get("rows")
        if not isinstance(rows_raw, list) or not rows_raw:
            return None
        if len(rows_raw) > MAX_REPLY_ROWS:
            return None
        rows: list[list[dict[str, Any]]] = []
        for row_raw in rows_raw:
            if not isinstance(row_raw, list) or not row_raw:
                return None
            if len(row_raw) > MAX_REPLY_ROW_BUTTONS:
                return None
            clean_row = [
                button
                for button in (_clean_reply_button(item) for item in row_raw)
                if button is not None
            ]
            if len(clean_row) != len(row_raw):
                return None
            rows.append(clean_row)
        placeholder = entry.get("placeholder", defaults[keyboard_id].get("placeholder", ""))
        if not isinstance(placeholder, str) or len(placeholder) > 120:
            return None
        persistent = bool(entry.get("persistent", False))
        result[keyboard_id] = {
            "rows": rows,
            "placeholder": placeholder.strip(),
            "persistent": persistent,
        }
    return result


def set_reply_keyboards(raw: Any) -> dict[str, dict[str, Any]]:
    global _reply_keyboards
    normalized = normalize_reply_keyboards(raw)
    _reply_keyboards = normalized or default_reply_keyboards()
    return _reply_keyboards


def current_reply_keyboards() -> dict[str, dict[str, Any]]:
    if _reply_keyboards:
        return {key: json.loads(json.dumps(value)) for key, value in _reply_keyboards.items()}
    return default_reply_keyboards()


def reply_keyboard_ids() -> list[dict[str, str]]:
    return [
        {"id": keyboard_id, "label": _REPLY_ID_LABELS[keyboard_id]}
        for keyboard_id in _REPLY_IDS
    ]


def _button_style_kwargs(item: dict[str, Any]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    style = item.get("style")
    if isinstance(style, str) and style in STYLE_VALUES:
        kwargs["style"] = style
    icon = item.get("icon")
    if isinstance(icon, str) and icon.isdigit():
        kwargs["icon_custom_emoji_id"] = icon
    return kwargs


def build_reply_keyboard(keyboard_id: str) -> Optional[ReplyKeyboardMarkup]:
    cfg = current_reply_keyboards().get(keyboard_id)
    if not cfg:
        return None
    app_url = mini_app_url()
    rows: list[list[KeyboardButton]] = []
    for row in cfg.get("rows", []):
        buttons: list[KeyboardButton] = []
        for item in row:
            kind = item.get("type", "text")
            kwargs = _button_style_kwargs(item)
            if kind == "web_app":
                if not app_url:
                    continue
                buttons.append(
                    KeyboardButton(
                        text=item["text"],
                        web_app=WebAppInfo(url=app_url),
                        **kwargs,
                    )
                )
            elif kind == "contact":
                buttons.append(
                    KeyboardButton(text=item["text"], request_contact=True, **kwargs)
                )
            elif kind == "location":
                buttons.append(
                    KeyboardButton(text=item["text"], request_location=True, **kwargs)
                )
            else:
                buttons.append(KeyboardButton(text=item["text"], **kwargs))
        if buttons:
            rows.append(buttons)
    if not rows:
        return None
    if keyboard_id == "app" and not app_url:
        return None
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        is_persistent=bool(cfg.get("persistent", False)),
        input_field_placeholder=cfg.get("placeholder") or None,
    )


def semantic_texts(kind: str) -> set[str]:
    values: set[str] = set()
    for cfg in current_reply_keyboards().values():
        for row in cfg.get("rows", []):
            for item in row:
                if item.get("type") == kind:
                    values.add(str(item.get("text", "")).strip())
    values.discard("")
    return values


def semantic_text(kind: str, fallback: str) -> str:
    values = semantic_texts(kind)
    return next(iter(values)) if values else fallback


def is_semantic_text(kind: str, text: Optional[str]) -> bool:
    if not text:
        return False
    return text.strip() in semantic_texts(kind)


# ----------------------------------------------------------------------
# إعدادات البوت
# ----------------------------------------------------------------------
def default_settings() -> dict[str, Any]:
    return {
        "mini_app_url": config.MINI_APP_URL or "",
        "default_admin_rights": {
            "is_anonymous": False,
            "can_manage_chat": True,
            "can_delete_messages": True,
            "can_manage_video_chats": True,
            "can_restrict_members": True,
            "can_promote_members": False,
            "can_change_info": True,
            "can_invite_users": True,
            "can_pin_messages": True,
            "can_manage_topics": True,
        },
        "default_admin_rights_customized": False,
    }


_RIGHTS_KEYS = tuple(default_settings()["default_admin_rights"].keys())


def normalize_settings(raw: Any) -> Optional[dict[str, Any]]:
    data = _loads(raw)
    if data is None:
        return None
    if not isinstance(data, dict):
        return None
    defaults = default_settings()
    url = data.get("mini_app_url", defaults["mini_app_url"])
    if not isinstance(url, str):
        return None
    url = url.strip()
    if url and (len(url) > MAX_URL or not url.lower().startswith("https://")):
        return None
    rights_raw = data.get("default_admin_rights", defaults["default_admin_rights"])
    if not isinstance(rights_raw, dict):
        return None
    rights = {}
    for key in _RIGHTS_KEYS:
        value = rights_raw.get(key, defaults["default_admin_rights"][key])
        if not isinstance(value, bool):
            return None
        rights[key] = value
    return {
        "mini_app_url": url,
        "default_admin_rights": rights,
        "default_admin_rights_customized": bool(
            data.get("default_admin_rights_customized", False)
        ),
    }


def set_settings(raw: Any) -> dict[str, Any]:
    global _settings
    normalized = normalize_settings(raw)
    _settings = normalized or default_settings()
    return _settings


def current_settings() -> dict[str, Any]:
    if _settings:
        return json.loads(json.dumps(_settings))
    return default_settings()


def mini_app_url() -> str:
    return str(current_settings().get("mini_app_url") or "").strip()


# ----------------------------------------------------------------------
# الأوامر (Commands + Scopes + Languages)
# ----------------------------------------------------------------------
ADMIN_COMMANDS: list[dict[str, str]] = [
    {"command": "start", "description": "فتح الواجهة الرئيسية"},
    {"command": "cancel", "description": "إلغاء العملية الحالية"},
    {"command": "reply", "description": "إرسال معلومات إضافية عن بلاغ"},
    {"command": "status", "description": "عرض بلاغاتي"},
    {"command": "chatid", "description": "معرّف هذه المحادثة (للإعداد)"},
    {"command": "admin", "description": "لوحة إدارة البلاغات"},
    {"command": "reports", "description": "عرض لوحة البلاغات"},
    {"command": "report", "description": "بدء بلاغ من المحادثة"},
    {"command": "stats", "description": "إحصائيات المستخدمين والبلاغات"},
    {"command": "backup", "description": "رفع نسخة احتياطية فورية"},
    {"command": "panic", "description": "إيقاف استقبال البلاغات طارئًا"},
    {"command": "resume", "description": "استئناف استقبال البلاغات"},
    {"command": "audit", "description": "عرض سجل التدقيق"},
    {"command": "app", "description": "فتح تطبيق البلاغ"},
    {"command": "help", "description": "عرض التعليمات"},
]


def default_commands_config() -> dict[str, Any]:
    scopes: list[dict[str, Any]] = [
        {
            "scope": "default",
            "chat_id": None,
            "language": "",
            "commands": [{"command": "start", "description": "فتح الواجهة الرئيسية"}],
        }
    ]
    for admin_id in sorted(getattr(config, "ADMIN_IDS", []) or []):
        scopes.append(
            {
                "scope": "chat",
                "chat_id": int(admin_id),
                "language": "",
                "commands": [dict(item) for item in ADMIN_COMMANDS],
            }
        )
    return {"scopes": scopes}


def _clean_commands(items: Any) -> Optional[list[dict[str, str]]]:
    if not isinstance(items, list):
        return None
    if len(items) > MAX_COMMANDS:
        return None
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            return None
        command = item.get("command")
        description = item.get("description")
        if not isinstance(command, str) or not isinstance(description, str):
            return None
        command = command.strip().lower()
        description = description.strip()
        if not _COMMAND_RE.match(command) or not 1 <= len(description) <= 256:
            return None
        if command in seen:
            return None
        seen.add(command)
        result.append({"command": command, "description": description})
    return result


def normalize_commands(raw: Any) -> Optional[dict[str, Any]]:
    data = _loads(raw)
    if data is None:
        return None
    if not isinstance(data, dict):
        return None
    scopes_raw = data.get("scopes")
    if not isinstance(scopes_raw, list) or not 1 <= len(scopes_raw) <= MAX_SCOPES:
        return None
    scopes: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, Optional[int], str]] = set()
    for item in scopes_raw:
        if not isinstance(item, dict):
            return None
        scope = item.get("scope")
        if scope not in _SCOPE_LABELS:
            return None
        chat_id = item.get("chat_id")
        if scope in ("chat", "chat_administrators"):
            if not isinstance(chat_id, int) or isinstance(chat_id, bool) or chat_id == 0:
                return None
        else:
            chat_id = None
        language = item.get("language", "")
        if not isinstance(language, str):
            return None
        language = language.strip()
        if language and not _LANG_RE.match(language):
            return None
        commands = _clean_commands(item.get("commands"))
        if commands is None:
            return None
        key = (scope, chat_id, language.lower())
        if key in seen_keys:
            return None
        seen_keys.add(key)
        scopes.append(
            {
                "scope": scope,
                "chat_id": chat_id,
                "language": language,
                "commands": commands,
            }
        )
    return {"scopes": scopes}


def set_commands_config(raw: Any) -> dict[str, Any]:
    global _commands_config
    normalized = normalize_commands(raw)
    _commands_config = normalized or default_commands_config()
    return _commands_config


def current_commands_config() -> dict[str, Any]:
    if _commands_config:
        return json.loads(json.dumps(_commands_config))
    return default_commands_config()


def _build_scope(item: dict[str, Any]):
    scope = item["scope"]
    chat_id = item.get("chat_id")
    if scope == "default":
        return BotCommandScopeDefault()
    if scope == "all_private_chats":
        return BotCommandScopeAllPrivateChats()
    if scope == "all_group_chats":
        return BotCommandScopeAllGroupChats()
    if scope == "all_chat_administrators":
        return BotCommandScopeAllChatAdministrators()
    if scope == "chat":
        return BotCommandScopeChat(chat_id=chat_id)
    if scope == "chat_administrators":
        return BotCommandScopeChatAdministrators(chat_id=chat_id)
    raise ValueError(f"unknown scope: {scope}")


async def apply_commands(bot, cfg: dict[str, Any]) -> dict[str, Any]:
    """تطبيق كل النطاقات على تيليجرام. يعيد قائمة الأخطاء دون إفشال الباقي."""
    errors: list[str] = []
    for item in cfg.get("scopes", []):
        try:
            scope = _build_scope(item)
            commands = [
                BotCommand(command=cmd["command"], description=cmd["description"])
                for cmd in item.get("commands", [])
            ]
            kwargs: dict[str, Any] = {"commands": commands, "scope": scope}
            if item.get("language"):
                kwargs["language_code"] = item["language"]
            await bot.set_my_commands(**kwargs)
        except Exception as exc:  # noqa: BLE001
            label = _SCOPE_LABELS.get(item.get("scope", ""), item.get("scope", ""))
            errors.append(f"{label}#{item.get('chat_id') or ''}: {exc}")
            logger.warning("تعذر تطبيق أوامر النطاق %s: %s", item, exc)
    return {"ok": not errors, "errors": errors}


async def delete_commands_scope(bot, item: dict[str, Any]) -> bool:
    try:
        kwargs: dict[str, Any] = {"scope": _build_scope(item)}
        if item.get("language"):
            kwargs["language_code"] = item["language"]
        await bot.delete_my_commands(**kwargs)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("تعذر حذف أوامر النطاق %s: %s", item, exc)
        return False


def removed_scopes(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """النطاقات التي حُذفت من اللوحة حتى تُحذف من تيليجرام أيضًا."""
    def key(item: dict[str, Any]) -> tuple[str, Optional[int], str]:
        return (item["scope"], item.get("chat_id"), item.get("language", "").lower())

    remaining = {key(item) for item in new.get("scopes", [])}
    return [item for item in old.get("scopes", []) if key(item) not in remaining]


# ----------------------------------------------------------------------
# زر القائمة (Menu Button)
# ----------------------------------------------------------------------
def default_menu_button() -> dict[str, Any]:
    return {"type": "default", "text": "فتح التطبيق", "url": ""}


def normalize_menu_button(raw: Any) -> Optional[dict[str, Any]]:
    data = _loads(raw)
    if data is None:
        return None
    if not isinstance(data, dict):
        return None
    kind = data.get("type", "default")
    if kind not in ("default", "commands", "web_app"):
        return None
    text = data.get("text", "فتح التطبيق")
    url = data.get("url", "")
    if not isinstance(text, str) or not isinstance(url, str):
        return None
    text = text.strip()
    url = url.strip()
    if kind == "web_app":
        if not 1 <= len(text) <= MAX_MENU_TEXT:
            return None
        if not url.lower().startswith("https://") or len(url) > MAX_URL:
            return None
    else:
        url = ""
    return {"type": kind, "text": text or "فتح التطبيق", "url": url}


def set_menu_button_config(raw: Any) -> dict[str, Any]:
    global _menu_button_config
    normalized = normalize_menu_button(raw)
    _menu_button_config = normalized or default_menu_button()
    return _menu_button_config


def current_menu_button_config() -> dict[str, Any]:
    if _menu_button_config:
        return dict(_menu_button_config)
    return default_menu_button()


async def apply_menu_button(bot, cfg: dict[str, Any]) -> None:
    kind = cfg.get("type", "default")
    if kind == "commands":
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    elif kind == "web_app":
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text=cfg["text"], web_app=WebAppInfo(url=cfg["url"])
            )
        )
    else:
        await bot.set_chat_menu_button(menu_button=MenuButtonDefault())


# ----------------------------------------------------------------------
# صلاحيات المشرف الافتراضية
# ----------------------------------------------------------------------
async def apply_default_admin_rights(bot, cfg: dict[str, Any]) -> None:
    if not cfg.get("default_admin_rights_customized"):
        return
    rights = ChatAdministratorRights(**cfg["default_admin_rights"])
    await bot.set_my_default_administrator_rights(rights=rights)


# ----------------------------------------------------------------------
# روابط عميقة وفحص الإنلاين
# ----------------------------------------------------------------------
def deep_links(username: str, payload: str) -> dict[str, str]:
    username = username.strip().lstrip("@")
    payload = payload.strip()[:MAX_DEEP_LINK_PAYLOAD]
    base = f"https://t.me/{username}" if username else ""
    return {
        "start": f"{base}?start={payload}" if base and payload else base,
        "startgroup": f"{base}?startgroup={payload}" if base and payload else base,
        "startapp": f"{base}?startapp={payload}" if base and payload else base,
        "plain": base,
    }


def sanitize_payload(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    # تيليجرام يقبل A-Z a-z 0-9 _ - فقط في معامل start
    return re.sub(r"[^A-Za-z0-9_-]", "", value)[:MAX_DEEP_LINK_PAYLOAD]


# ----------------------------------------------------------------------
# تحميل كل الحالة عند الإقلاع
# ----------------------------------------------------------------------
async def load_from_state(db) -> dict[str, Any]:
    reply_raw = await db.get_state(REPLY_STATE_KEY)
    commands_raw = await db.get_state(COMMANDS_STATE_KEY)
    menu_raw = await db.get_state(MENU_BUTTON_STATE_KEY)
    settings_raw = await db.get_state(SETTINGS_STATE_KEY)
    set_reply_keyboards(reply_raw)
    set_commands_config(commands_raw)
    set_menu_button_config(menu_raw)
    set_settings(settings_raw)
    return {
        "reply_customized": normalize_reply_keyboards(reply_raw) is not None,
        "commands_customized": normalize_commands(commands_raw) is not None,
        "menu_customized": normalize_menu_button(menu_raw) is not None,
        "settings_customized": normalize_settings(settings_raw) is not None,
    }
