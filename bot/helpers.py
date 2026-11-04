"""مساعدات تدفقات البوت.

نُقلت من ``bot/handlers.py`` مع إعادة استيراد أسمائها هناك، فلا يتغير
أي تسجيل أو استدعاء.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from telegram import (
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes, filters

from bot import control, keyboards, texts
from database import Database
from services import guards, utils

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _typing_while_processing(bot, chat_id: int):
    """Keep Telegram's native typing animation visible during slow work."""
    stopped = asyncio.Event()

    async def pulse() -> None:
        while not stopped.is_set():
            try:
                await bot.send_chat_action(
                    chat_id=chat_id,
                    action=ChatAction.TYPING,
                )
            except TelegramError:
                logger.info("تعذر إرسال مؤشر الكتابة للمستخدم %s", chat_id, exc_info=True)
            try:
                await asyncio.wait_for(stopped.wait(), timeout=4)
            except asyncio.TimeoutError:
                continue

    task = asyncio.create_task(pulse())
    try:
        yield
    finally:
        stopped.set()
        await task


# مراحل المحادثة
(
    ASK_CATEGORY,
    ASK_PLATFORM,
    ASK_DESC,
    ASK_PHOTO,
    ASK_LINK,
    ASK_LOCATION,
    ASK_CONTACT,
    CONFIRM,
    ASK_FOLLOWUP,
    ASK_SUPPORT,
    ASK_VERIFICATION_PHONE,
    ASK_VERIFICATION_SELFIE,
    ASK_VERIFICATION_ID_FRONT,
    ASK_VERIFICATION_ID_BACK,
    ASK_REPORT_APPEAL,
) = range(15)
ASK_VERIFICATION_SUPPLEMENT = 15
ASK_TYPE = 16
SUPPORT_MAX_LENGTH = 1000
SUPPORT_MAX_WORDS = 100
SUPPORT_MAX_PER_10_MINUTES = 3
SUPPORT_MAX_PER_DAY = 10
VERIFICATION_TICKET_KEY = "verification_ticket_hash"
VERIFICATION_CAPTURES_KEY = "verification_captures"
VERIFICATION_STEP_KEY = "verification_step"
VERIFICATION_TICKET_MINUTES = 30

# نصوص الأزرار (أنماط regex) لإلغاء العملية وتأكيد الحفظ
CANCEL_RE = r"^\s*❌\s*إلغاء\s*$"
CONFIRM_RE = r"^\s*✅\s*إرسال\s+البلاغ\s*$"


class _SemanticTextFilter(filters.MessageFilter):
    """يطابق نص زر لوحة الرد بعد تعديله من اللوحة (وليس النص القديم فقط)."""

    def __init__(self, kind: str) -> None:
        super().__init__()
        self.kind = kind

    def filter(self, message) -> bool:  # noqa: ANN001
        return control.is_semantic_text(self.kind, message.text)

# بادئة استدعاءات المستخدم (تعريف أصلي في keyboards — يُستعمل هنا للفحص السريع)
USR_PREFIX = "usr:"

# الحد الأقصى للبطاقات المعروضة في شاشة «بلاغاتي» (تُجلب واحدة زيادة للكشف عن المزيد)
MY_REPORTS_MAX = 6
ACTIVE_PROMPT_KEY = "_active_user_prompt"

# ==================================================================
# طلبات التطبيق (Mini App): المستخدم الذي أرسل بياناته وينتظر الصورة
# ==================================================================
_pending_app_users: set[int] = set()


class AppPhotoPending(filters.MessageFilter):
    """يسمّي الرسائل التي تخصّ طلبًا مفتوحًا من التطبيق."""

    def filter(self, message) -> bool:
        user = message.from_user
        return bool(user and user.id in _pending_app_users)


def _db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.bot_data["db"]


async def _welcome_content(db: Database) -> tuple[str, ParseMode | None]:
    raw = await db.get_state("bot_profile")
    try:
        profile = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        profile = {}
    custom = profile.get("welcome_text") if isinstance(profile, dict) else None
    if isinstance(custom, str) and custom.strip():
        return custom.strip(), None
    managed = await db.get_bot_text("welcome_message")
    if managed and managed.strip():
        return managed.strip(), ParseMode.HTML
    return texts.WELCOME, ParseMode.HTML


async def _main_menu_content(
    db: Database, user_id: int | None = None
) -> tuple[str, ParseMode | None]:
    welcome, parse_mode = await _welcome_content(db)
    if await guards.is_emergency(db):
        if parse_mode == ParseMode.HTML:
            welcome = texts.REPORTING_PAUSED + "\n\n" + welcome
        else:
            welcome = texts.INLINE_MESSAGES["reporting_paused_plain"] + welcome
    return await _with_verified_reporter_badge(welcome, db, user_id), parse_mode


VERIFIED_REPORTER_BADGE = texts.INLINE_MESSAGES["verified_reporter_badge"]


async def _is_verified_reporter(db, user_id: int | None) -> bool:
    status_getter = getattr(db, "verification_status", None)
    if not user_id or status_getter is None:
        return False
    status = await status_getter(user_id)
    return bool(status and status.get("status") == "verified")


async def _with_verified_reporter_badge(
    text: str, db, user_id: int | None
) -> str:
    if (
        user_id
        and not text.startswith(VERIFIED_REPORTER_BADGE)
        and await _is_verified_reporter(db, user_id)
    ):
        return f"{VERIFIED_REPORTER_BADGE}\n\n{text}"
    return text


async def _privacy_policy_url(db: Database) -> str | None:
    raw = await db.get_state("bot_profile")
    try:
        profile = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return None
    if not isinstance(profile, dict):
        return None
    url = str(profile.get("privacy_policy_url") or "").strip()
    return url if url.startswith("https://") else None


async def _verification_consent(db: Database) -> tuple[str, str]:
    raw = await db.get_state("bot_profile")
    try:
        profile = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        profile = {}
    custom = profile.get("verification_consent_text") if isinstance(profile, dict) else None
    supplemental = custom.strip() if isinstance(custom, str) else ""
    text = await texts.get_text(texts.VERIFICATION_CONSENT_DB_KEY, texts.VERIFICATION_CONSENT_DEFAULT)
    if supplemental:
        text += "\n\nإيضاحات إضافية من الإدارة:\n" + supplemental
    privacy_url = await _privacy_policy_url(db) or ""
    version = hashlib.sha256(
        (text + "\n" + privacy_url).encode("utf-8")
    ).hexdigest()[:24]
    return text, version


def _remember_user_prompt(
    context: ContextTypes.DEFAULT_TYPE,
    message,
    keyboard_kind: str = "inline",
) -> None:
    if message is None:
        return
    chat_id = getattr(message, "chat_id", None)
    message_id = getattr(message, "message_id", None)
    if chat_id is not None and message_id is not None:
        context.user_data[ACTIVE_PROMPT_KEY] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "keyboard_kind": keyboard_kind,
        }


async def _delete_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if (
        message is None
        or user is None
        or message.from_user is None
        or message.from_user.id != user.id
        or message.message_id is None
    ):
        return
    try:
        await context.bot.delete_message(
            chat_id=message.chat_id,
            message_id=message.message_id,
        )
    except TelegramError:
        logger.info(
            "تعذر حذف رسالة المستخدم المؤقتة %s",
            message.message_id,
            exc_info=True,
        )


async def _clear_user_prompt(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    prompt = context.user_data.pop(ACTIVE_PROMPT_KEY, None)
    if not prompt:
        return None
    try:
        await context.bot.delete_message(
            chat_id=prompt["chat_id"], message_id=prompt["message_id"]
        )
    except TelegramError as exc:
        logger.info(
            "تعذر حذف رسالة البوت المؤقتة %s؛ ستُزال أزرارها إن أمكن: %s",
            prompt["message_id"],
            exc,
        )
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=prompt["chat_id"],
                message_id=prompt["message_id"],
                reply_markup=None,
            )
        except TelegramError:
            logger.warning(
                "تعذر تنظيف رسالة البوت المؤقتة %s",
                prompt["message_id"],
                exc_info=True,
            )
    return prompt.get("keyboard_kind")


async def _clear_welcome_photo(context: ContextTypes.DEFAULT_TYPE) -> None:
    photo = context.user_data.pop("_welcome_photo_prompt", None)
    if not isinstance(photo, dict):
        return
    try:
        await context.bot.delete_message(
            chat_id=photo["chat_id"],
            message_id=photo["message_id"],
        )
    except TelegramError:
        logger.info(
            "تعذر حذف صورة الترحيب المؤقتة %s",
            photo.get("message_id"),
            exc_info=True,
        )


async def _send_clean_prompt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    *,
    parse_mode: ParseMode | None = None,
    reply_markup=None,
):
    """Replace the previous temporary bot prompt instead of stacking prompts."""
    query = getattr(update, "callback_query", None)
    user = getattr(update, "effective_user", None)
    if user is None and query is not None:
        user = query.from_user
    db = getattr(context, "bot_data", {}).get("db")
    if db is not None:
        text = await _with_verified_reporter_badge(
            text, db, getattr(user, "id", None)
        )
    if query and not isinstance(reply_markup, ReplyKeyboardMarkup):
        try:
            message = await query.edit_message_text(
                text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            kind = (
                "reply"
                if isinstance(reply_markup, ReplyKeyboardMarkup)
                else "plain"
                if reply_markup is None
                else "inline"
            )
            _remember_user_prompt(context, message or query.message, kind)
            return message or query.message
        except BadRequest as exc:
            if "message is not modified" in str(exc).lower():
                _remember_user_prompt(context, query.message, "inline")
                return query.message
            logger.info("تعذر تحديث رسالة البوت الحالية؛ سيُستبدل بها غيرها: %s", exc)

    previous_kind = await _clear_user_prompt(context)
    remove_reply_keyboard = (
        previous_kind == "reply"
        and not isinstance(reply_markup, (ReplyKeyboardMarkup, ReplyKeyboardRemove))
    )
    send_markup = ReplyKeyboardRemove() if remove_reply_keyboard else reply_markup
    if query:
        message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=send_markup,
        )
    else:
        message = await update.effective_message.reply_text(
            text,
            parse_mode=parse_mode,
            reply_markup=send_markup,
        )
    if remove_reply_keyboard and isinstance(reply_markup, InlineKeyboardMarkup):
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=message.chat_id,
                message_id=message.message_id,
                reply_markup=reply_markup,
            )
        except TelegramError:
            logger.warning(
                "تعذر إضافة أزرار القائمة بعد إزالة لوحة الرد؛ سيُرسل بديل",
                exc_info=True,
            )
            replacement = await context.bot.send_message(
                chat_id=message.chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            try:
                await context.bot.delete_message(
                    chat_id=message.chat_id,
                    message_id=message.message_id,
                )
            except TelegramError:
                logger.info(
                    "تعذر حذف رسالة الانتقال بعد إرسال القائمة البديلة %s",
                    message.message_id,
                    exc_info=True,
                )
            message = replacement
    kind = (
        "reply"
        if isinstance(reply_markup, ReplyKeyboardMarkup)
        else "plain"
        if reply_markup is None
        else "inline"
    )
    _remember_user_prompt(context, message, kind)
    return message


def _contact_back_step(context: ContextTypes.DEFAULT_TYPE) -> int:
    category = context.user_data.get("report_category")
    if category == "field":
        return ASK_LOCATION
    if category == "social":
        return ASK_LINK
    return ASK_PHOTO


