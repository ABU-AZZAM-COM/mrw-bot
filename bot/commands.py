"""أوامر البوت ورسائل المستخدم.

نُقلت من ``bot/handlers.py`` مع إعادة استيراد أسمائها هناك.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import secrets
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from telegram import (
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import ContextTypes, ConversationHandler, filters

from app import config
from bot import admin, control, keyboards, texts
from bot import helpers as _helpers
from bot.helpers import (
    ACTIVE_PROMPT_KEY,
    ASK_FOLLOWUP,
    ASK_REPORT_APPEAL,
    MY_REPORTS_MAX,
    _db,
    _delete_user_message,
    _pending_app_users,
    _typing_while_processing,
)
from bot.keyboards import back_to_menu_kb, my_reports_kb
from database import Database, STATUS_AWAITING_INFO, STATUS_REJECTED
from services import guards, utils

logger = logging.getLogger(__name__)


def _late_bound(name: str):
    """غلاف يحلّ الاسم من ``bot.handlers`` وقت الطلب لا وقت التركيب."""

    def call(*args, **kwargs):
        from bot import handlers

        return getattr(handlers, name)(*args, **kwargs)

    return call


VERIFIED_REPORTER_BADGE = _helpers.VERIFIED_REPORTER_BADGE
_discard_verification_capture = _late_bound("_discard_verification_capture")
_main_menu_content = _late_bound("_main_menu_content")
_send_clean_prompt = _late_bound("_send_clean_prompt")
main_menu_kb = _late_bound("main_menu_kb")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دليل الاستخدام المختصر — يعمل في أي لحظة (حتى أثناء محادثة نشطة)."""
    await update.effective_message.reply_text(
        texts.HELP,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(update.effective_user.id if update.effective_user else None),
    )


# ==================================================================
# أمر /status — نفس زر «📊 حالة بلاغي» في رسالة مباشرة
# ==================================================================
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بطاقات «بلاغاتي» بأمر كتابي — لمن يخطر له ذلك دون فتح القائمة."""
    chat = update.effective_chat
    if chat is None or not guards.is_private_chat(chat.type):
        await update.effective_message.reply_text(
            texts.PRIVATE_ONLY.format(bot=config.BOT_USERNAME),
            parse_mode=ParseMode.HTML,
        )
        return
    uid = update.effective_user.id
    reports, total = await _db(context).list_user_reports(uid, limit=MY_REPORTS_MAX + 1)
    if not reports:
        await update.effective_message.reply_text(
            texts.NO_REPORTS_YET, parse_mode=ParseMode.HTML,
            reply_markup=back_to_menu_kb(),
        )
        return
    await update.effective_message.reply_text(
        _my_reports_text(reports, total),
        parse_mode=ParseMode.HTML,
        reply_markup=my_reports_kb(reports[:MY_REPORTS_MAX]),
    )


async def cmd_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Accept reporter follow-up only for a report awaiting requested information."""
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None or len(context.args) < 2:
        if message is not None:
            await message.reply_text(texts.INLINE_MESSAGES["reply_usage"])
        return
    if chat is None or chat.type != "private":
        await message.reply_text(texts.INLINE_MESSAGES["reply_private_only"])
        return

    report = await _db(context).get_by_seq(context.args[0].strip())
    if (
        not report
        or int(report.get("user_id") or 0) != user.id
        or not report.get("followup_requested_at")
        or report.get("status") != STATUS_AWAITING_INFO
        or (
            report.get("followup_due_at")
            and report["followup_due_at"]
            <= datetime.now(timezone.utc).isoformat(timespec="seconds")
        )
    ):
        await message.reply_text(texts.INLINE_MESSAGES["followup_unavailable"])
        return

    response = " ".join(context.args[1:]).strip()
    if not 2 <= len(response) <= 2000:
        await message.reply_text(texts.INLINE_MESSAGES["followup_length"])
        return

    db = _db(context)
    await db.save_followup_response(int(report["id"]), response)
    await db.audit(
        user.id, "report_followup_received", str(report["id"]),
        f"عدد الأحرف: {len(response)}",
    )
    delivered = 0
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=texts.FOLLOWUP_ADMIN_NOTIF.format(
                    seq=report.get("seq") or report["id"],
                    name=user.full_name,
                    user_id=user.id,
                    message=response,
                ),
            )
            delivered += 1
        except Exception:
            logger.warning(
                "تعذر إيصال معلومات متابعة البلاغ %s للمشرف %s",
                report["id"], admin_id,
            )
    if delivered:
        await message.reply_text(texts.INLINE_MESSAGES["followup_received"])
    else:
        await message.reply_text(texts.INLINE_MESSAGES["followup_saved_no_notify"])


async def receive_support_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Retire the legacy in-bot support flow without forwarding user content."""
    message = update.effective_message
    if message:
        context.user_data.pop("support_pending", None)
        await message.reply_text(
            texts.WELCOME_HINT,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(
                update.effective_user.id if update.effective_user else None
            ),
        )
    return ConversationHandler.END


async def receive_followup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    response = (message.text or "").strip()
    rid = context.user_data.get("followup_report_id")
    if not isinstance(rid, int) or not 2 <= len(response) <= 2000:
        await message.reply_text(texts.INLINE_MESSAGES["followup_length"])
        return ASK_FOLLOWUP
    db = _db(context)
    report = await db.get_by_id(rid)
    if (
        not report
        or int(report.get("user_id") or 0) != user.id
        or not report.get("followup_requested_at")
        or report.get("followup_response")
        or report.get("status") != STATUS_AWAITING_INFO
        or (
            report.get("followup_due_at")
            and report["followup_due_at"]
            <= datetime.now(timezone.utc).isoformat(timespec="seconds")
        )
    ):
        context.user_data.pop("followup_report_id", None)
        await message.reply_text(texts.INLINE_MESSAGES["followup_unavailable"])
        return ConversationHandler.END

    await db.save_followup_response(rid, response)
    await db.audit(
        user.id, "report_followup_received", str(rid),
        f"عدد الأحرف: {len(response)}",
    )
    delivered = 0
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=texts.FOLLOWUP_ADMIN_NOTIF.format(
                    seq=utils.esc(report.get("seq") or rid),
                    name=utils.esc(user.full_name or "-"),
                    user_id=user.id,
                    message=utils.esc(response),
                ),
                parse_mode=ParseMode.HTML,
            )
            delivered += 1
        except Exception:
            logger.warning("تعذر إيصال متابعة البلاغ %s للمشرف %s", rid, admin_id)
    context.user_data.pop("followup_report_id", None)
    await message.reply_text(
        texts.INLINE_MESSAGES["followup_received"]
        if delivered
        else texts.INLINE_MESSAGES["followup_saved_no_notify"]
    )
    return ConversationHandler.END


async def receive_report_appeal(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    report_id = context.user_data.get("report_appeal_id")
    text = (message.text or "").strip() if message else ""
    if (
        message is None
        or user is None
        or chat is None
        or chat.type != "private"
        or not isinstance(report_id, int)
        or not 2 <= len(text) <= 2000
    ):
        if message:
            await message.reply_text(texts.INLINE_MESSAGES["appeal_length"])
        return ASK_REPORT_APPEAL

    db = _db(context)
    appeal_id = await db.add_report_appeal(report_id, user.id, text)
    if appeal_id is None:
        context.user_data.pop("report_appeal_id", None)
        await message.reply_text(texts.INLINE_MESSAGES["appeal_unavailable"])
        return ConversationHandler.END
    report = await db.get_by_id(report_id)
    delivered = 0
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=texts.APPEAL_ADMIN_NOTIF.format(
                    seq=utils.esc(report.get("seq") or report_id),
                    name=utils.esc(user.full_name or "-"),
                    user_id=user.id,
                    message=utils.esc(text),
                ),
                parse_mode=ParseMode.HTML,
            )
            delivered += 1
        except Exception:
            logger.warning(
                "تعذر إيصال اعتراض البلاغ %s للمشرف %s", report_id, admin_id
            )
    context.user_data.pop("report_appeal_id", None)
    await message.reply_text(
        texts.INLINE_MESSAGES["appeal_saved_notified"]
        if delivered
        else texts.INLINE_MESSAGES["appeal_saved_no_notify"],
        reply_markup=main_menu_kb(update.effective_user.id if update.effective_user else None),
    )
    return ConversationHandler.END


# ==================================================================
# أداة الإعداد: معرفة معرّف المحادثة (يُستخدم لضبط القناة الرسمية)
# ==================================================================
async def cmd_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يردّ بمعرّف المحادثة الحالية — ضروري لمعرفة ARCHIVE_CHAT_ID.

    وعند تنفيذ الأمر داخل قناة، يُرسَل المعرّف أيضًا إلى أول مشرف في محادثته
    الخاصة، فيصله مباشرة دون حاجة لنسخه يدويًا.
    """
    chat = update.effective_chat
    user = update.effective_user
    message = texts.CHAT_ID.format(
        chat_id=chat.id,
        title=utils.esc(chat.title or chat.full_name or "-"),
        chat_type=chat.type,
        user_id=user.id,
    )
    await update.effective_message.reply_text(message, parse_mode=ParseMode.HTML)

    # إرسال المعرّف إلى المشرف في محادثة خاصة (تسهيل خطوة الإعداد)
    if chat.type != "private" and config.ADMIN_IDS:
        note = message + texts.CHAT_ID_ADMIN_NOTE.format(chat_id=chat.id)
        for admin_id in config.ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=note,
                    parse_mode=ParseMode.HTML,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("تعذر إرسال معرّف المحادثة إلى المشرف %s: %s", admin_id, exc)
            break

    # تسجيل المعرّف في السجل ليكون بالإمكان قراءته آليًا أثناء الإعداد
    logger.info(
        "📌 معرّف المحادثة الحالي (للإعداد): %s | النوع: %s | الاسم: %s",
        chat.id,
        chat.type,
        chat.title or chat.full_name or "-",
    )


# ==================================================================
# إلغاء العملية (أمر أو زر)
# ==================================================================
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat is None or not guards.is_private_chat(chat.type):
        await update.effective_message.reply_text(
            texts.PRIVATE_ONLY.format(bot=config.BOT_USERNAME),
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END
    await _discard_verification_capture(
        context,
        update.effective_user.id if update.effective_user else None,
    )
    active_prompt = context.user_data.get(ACTIVE_PROMPT_KEY)
    context.user_data.clear()
    if active_prompt:
        context.user_data[ACTIVE_PROMPT_KEY] = active_prompt
    if update.effective_user:
        _pending_app_users.discard(update.effective_user.id)
    await _send_clean_prompt(
        update,
        context,
        texts.CANCELLED,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(update.effective_user.id if update.effective_user else None),
    )
    await _delete_user_message(update, context)
    return ConversationHandler.END


async def verification_phone_back(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    active_prompt = context.user_data.get(ACTIVE_PROMPT_KEY)
    context.user_data.clear()
    if active_prompt:
        context.user_data[ACTIVE_PROMPT_KEY] = active_prompt
    if update.effective_user:
        _pending_app_users.discard(update.effective_user.id)
    user_id = update.effective_user.id if update.effective_user else None
    welcome, parse_mode = await _main_menu_content(_db(context), user_id)
    await _send_clean_prompt(
        update,
        context,
        welcome,
        parse_mode=parse_mode,
        reply_markup=main_menu_kb(user_id),
    )
    await _delete_user_message(update, context)
    return ConversationHandler.END


# ==================================================================
# معالج استدعاءات الأزرار المضمنة للمستخدم (usr:*)
# ==================================================================
def _my_reports_text(
    reports: list, total: int, page: int = 0, *, verified: bool = False
) -> str:
    """نص شاشة «بلاغاتي»: بطاقة مختصرة لكل بلاغ (رقمها وحالتها وزمنها)."""
    shown = reports[:MY_REPORTS_MAX]
    lines = [
        f"▣ <b><code>{r['seq']}</code></b> — {texts.badge(r['status'])}\n"
        f"🕒 {utils.fmt_local(r['created_at'])}\n"
        for r in shown
    ]
    page_count = max(1, (total + MY_REPORTS_MAX - 1) // MY_REPORTS_MAX)
    has_next = (page + 1) * MY_REPORTS_MAX < total
    more = texts.MY_REPORTS_MORE if has_next else ""
    page_label = f"\nالصفحة {page + 1} من {page_count}\n"
    content = texts.MY_REPORTS_HEADER.format(
        count=total, lines="".join(lines) + page_label + more
    )
    return f"{VERIFIED_REPORTER_BADGE}\n\n{content}" if verified else content


