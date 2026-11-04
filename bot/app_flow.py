"""تدفق التطبيق المصغّر.

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
from bot.helpers import (
    _db,
    _main_menu_content,
    _pending_app_users,
    _send_clean_prompt,
    _typing_while_processing,
    _with_verified_reporter_badge,
)
from database import Database, STATUS_AWAITING_INFO, STATUS_REJECTED
from services import archive, guards, hub_backup, utils
from services.report_taxonomy import REPORT_CATEGORIES

logger = logging.getLogger(__name__)


def _late_bound(name: str):
    """غلاف يحلّ الاسم من ``bot.handlers`` وقت الطلب لا وقت التركيب."""

    def call(*args, **kwargs):
        from bot import handlers

        return getattr(handlers, name)(*args, **kwargs)

    return call


def _report_flow_bound(name: str):
    """غلاف يحلّ الاسم من ``bot.report_flow`` وقت الطلب."""

    def call(*args, **kwargs):
        from bot import report_flow

        return getattr(report_flow, name)(*args, **kwargs)

    return call


_notify_admins = _late_bound("_notify_admins")
_save_photo = _report_flow_bound("_save_photo")
_freeze_for_sensitive_image = _report_flow_bound("_freeze_for_sensitive_image")


async def cmd_app(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يرسل زر فتح التطبيق (Mini App) للمستخدم."""
    keyboard = keyboards_app_kb()
    if keyboard is None:
        await update.effective_message.reply_text(
            texts.APP_NOT_READY, parse_mode=ParseMode.HTML
        )
        return

    await update.effective_message.reply_text(
        texts.APP_INTRO, parse_mode=ParseMode.HTML, reply_markup=keyboard
    )


def keyboards_app_kb():
    """اختصار آمن لاستدعاء keyboards.app_kb (يتعامل مع غياب المكتبة)."""
    from keyboards import app_kb

    return app_kb()


# ==================================================================
# استقبال بيانات التطبيق (Mini App) ثم الصورة وإتمام البلاغ
# ==================================================================
async def app_data_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يستقبل ما ملأه المستخدم في التطبيق (نوع المخالفة، الموقع، الوصف، الرابط).

    ملاحظة أمنية: البيانات تصل عبر تلغرام نفسها (web_app_data) فهي موثّقة
    النسبة للمستخدم، وكل الفحوص الأمنية (المعدّل، التكرار، المضمون) تُطبَّق هنا
    تمامًا كما في مسار المحادثة العادي.
    """
    user = update.effective_user
    chat = update.effective_chat
    db = _db(context)

    # الحماية ③: التطبيق يعمل من المحادثة الخاصة فقط
    if not guards.is_private_chat(chat.type):
        await update.effective_message.reply_text(
            texts.PRIVATE_ONLY.format(bot=config.BOT_USERNAME), parse_mode=ParseMode.HTML
        )
        return None

    # الحماية: الإيقاف الطارئ + حدود المعدّل
    if await guards.is_emergency(db):
        await update.effective_message.reply_text(
            await guards.render_block_message(db, "emergency"),
            parse_mode=ParseMode.HTML,
        )
        return None

    verdict = await guards.check_rate_limits(db, user.id)
    if verdict.blocked:
        await update.effective_message.reply_text(verdict.message, parse_mode=ParseMode.HTML)
        return None

    daily = await guards.check_daily_limit(db, user.id)
    if daily.blocked:
        await update.effective_message.reply_text(daily.message, parse_mode=ParseMode.HTML)
        return None

    # قراءة البيانات وتحويلها إلى قاموس آمن
    raw = update.effective_message.web_app_data.data or "{}"
    try:
        import json

        data = json.loads(raw)
    except Exception:
        logger.warning("بيانات تطبيق غير صالحة من المستخدم %s", user.id)
        await update.effective_message.reply_text(
            texts.APP_BAD_DATA, parse_mode=ParseMode.HTML
        )
        return None
    if not isinstance(data, dict):
        await update.effective_message.reply_text(
            texts.APP_BAD_DATA, parse_mode=ParseMode.HTML
        )
        return None

    violation_type = str(data.get("type") or "").strip()
    category_key = str(data.get("category") or "").strip()
    if category_key and category_key not in REPORT_CATEGORIES:
        await update.effective_message.reply_text(
            texts.INLINE_MESSAGES["unknown_category"],
            parse_mode=ParseMode.HTML,
        )
        return None
    if category_key and not violation_type:
        await update.effective_message.reply_text(
            texts.INLINE_MESSAGES["violation_type_unavailable"],
            parse_mode=ParseMode.HTML,
        )
        return None
    if violation_type:
        if len(violation_type) > 60:
            await update.effective_message.reply_text(
                texts.INLINE_MESSAGES["violation_type_unavailable"],
                parse_mode=ParseMode.HTML,
            )
            return None
        if category_key:
            allowed_types = (
                await db.effective_vtype_groups()
            ).get(category_key, [])
        else:
            allowed_types = await db.effective_vtypes()
        if violation_type not in allowed_types:
            await update.effective_message.reply_text(
                texts.INLINE_MESSAGES["violation_type_unavailable"],
                parse_mode=ParseMode.HTML,
            )
            return None
    place = str(data.get("place") or "")[:120]
    description = str(data.get("desc") or "").strip()
    if len(description) > config.MAX_DESC_LEN:
        await update.effective_message.reply_text(
            texts.DESC_TOO_LONG.format(
                length=len(description), max=config.MAX_DESC_LEN
            ),
            parse_mode=ParseMode.HTML,
        )
        return None
    link = str(data.get("link") or "").strip()
    lat = data.get("lat")
    lon = data.get("lon")

    link_verdict, substance_verdict, duplicate = (
        await guards.inspect_report_content(db, link, description)
    )
    if not link and link_verdict.code == "bad_url":
        link_verdict.message = texts.LINK_REQUIRED
    if link_verdict.blocked:
        await update.effective_message.reply_text(
            link_verdict.message, parse_mode=ParseMode.HTML
        )
        return None
    if substance_verdict is not None and substance_verdict.blocked:
        await update.effective_message.reply_text(
            substance_verdict.message, parse_mode=ParseMode.HTML
        )
        return None

    platform = link_verdict.extra.get("platform") or ""
    if link_verdict.code == "unknown_platform":
        await update.effective_message.reply_text(
            link_verdict.message, parse_mode=ParseMode.HTML
        )
    if link_verdict.extra.get("needs_review"):
        await update.effective_message.reply_text(
            texts.VIRUSTOTAL_REVIEW, parse_mode=ParseMode.HTML
        )

    await db.log_event(user.id, "report")

    context.user_data.clear()
    context.user_data["app_draft"] = {
        "category": REPORT_CATEGORIES.get(category_key),
        "violation_type": violation_type,
        "place": place,
        "description": description,
        "post_link": link,
        "platform": platform,
        "lat": float(lat) if isinstance(lat, (int, float)) else None,
        "lon": float(lon) if isinstance(lon, (int, float)) else None,
        "suspect_duplicate": (
            duplicate is not None
            and duplicate.code in {"exact_duplicate", "similar_duplicate"}
        ) or link_verdict.code == "unknown_platform"
        or bool(link_verdict.extra.get("needs_review")),
        "duplicate_of": (
            duplicate.extra.get("duplicate_of") if duplicate is not None else None
        ),
    }
    _pending_app_users.add(user.id)

    review_notice = (
        texts.VIRUSTOTAL_REVIEW
        if link_verdict.extra.get("needs_review")
        else ""
    )
    app_data_confirmation = texts.APP_DATA_OK.format(
        vtype=utils.esc(
            violation_type or texts.INLINE_MESSAGES["unspecified_value"]
        ),
        place=utils.esc(place or texts.INLINE_MESSAGES["unspecified_value"]),
        review=(
            utils.esc(review_notice) + "\n\n"
            if review_notice
            else (
                texts.INLINE_MESSAGES["app_duplicate_manual_review"]
                if duplicate and duplicate.code in {"exact_duplicate", "similar_duplicate"}
                else ""
            )
        ),
    )
    await update.effective_message.reply_text(
        await _with_verified_reporter_badge(
            app_data_confirmation, db, user.id
        ),
        parse_mode=ParseMode.HTML,
    )
    await update.effective_message.reply_text(
        await _with_verified_reporter_badge(texts.APP_SEND_PHOTO, db, user.id),
        parse_mode=ParseMode.HTML,
    )
    return None


async def app_photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يستقبل الصورة بعد إرسال بيانات التطبيق ثم يُنشئ البلاغ كاملًا."""
    user = update.effective_user
    db = _db(context)
    ud = context.user_data
    draft = ud.get("app_draft")

    if not draft:
        _pending_app_users.discard(user.id)
        return None

    # حفظ الصورة بنفس المنطق المستخدم في المحادثة العادية
    progress = await update.effective_message.reply_text(
        texts.INLINE_MESSAGES["verification_checking_photo"]
    )
    photo_path, photo_file_id, photo_name, screening = await _save_photo(
        update, context
    )
    if not photo_file_id:
        await progress.edit_text(texts.INLINE_MESSAGES["photo_receipt_incomplete"])
        await update.effective_message.reply_text(
            texts.PHOTO_FAILED, parse_mode=ParseMode.HTML
        )
        return None
    if screening.flagged:
        await progress.edit_text(texts.INLINE_MESSAGES["photo_check_interrupted"])
        await _freeze_for_sensitive_image(
            update, context, screening, photo_path, photo_file_id
        )
        return None
    await progress.edit_text(
        texts.INLINE_MESSAGES["photo_check_review_required"]
        if screening.review_required
        else texts.INLINE_MESSAGES["photo_check_passed"]
    )

    try:
        report = await db.add_report(
            user_id=user.id,
            username=user.username,
            full_name=user.full_name,
            photo_path=photo_path,
            photo_file_id=photo_file_id,
            post_link=draft["post_link"],
            description=draft["description"],
            violation_type=draft["violation_type"],
            place=draft["place"],
            latitude=draft["lat"],
            longitude=draft["lon"],
            source="app",
            platform=draft.get("platform") or "",
            category=draft.get("category"),
            needs_review=1 if draft["suspect_duplicate"] or screening.review_required else 0,
            admin_note=context.user_data.get("image_moderation_flag") or None,
        )
    except Exception:
        logger.exception("فشل حفظ بلاغ التطبيق للمستخدم %s", user.id)
        await update.effective_message.reply_text(
            texts.GENERAL_ERROR, parse_mode=ParseMode.HTML
        )
        return None

    ud.pop("app_draft", None)
    _pending_app_users.discard(user.id)
    logger.info("بلاغ جديد (تطبيق) %s من المستخدم %s", report["seq"], user.id)
    await update.effective_message.reply_text(
        await _with_verified_reporter_badge(
            texts.SAVED.format(
                seq=report["seq"],
                time=utils.fmt_local(report["created_at"]),
                status=texts.badge(report["status"]),
            ),
            db,
            user.id,
        ),
        parse_mode=ParseMode.HTML,
    )
    await _notify_admins(context, report)

    # الفهرسة المساعدة لا ينبغي أن تحجب تأكيد البلاغ المحفوظ.
    try:
        await db.add_fingerprint(
            report["id"],
            guards.link_hash(draft["post_link"]) if draft["post_link"] else None,
            guards.text_hash(draft["description"]),
            None,
        )
    except Exception:
        logger.exception("تعذرت إضافة بصمة بلاغ التطبيق %s", report["seq"])

    if config.AUTO_PUBLISH_TO_ARCHIVE:
        await archive.post_report(context.bot, report)
    if config.DB_BACKEND == "sqlite":
        await archive.backup_database(context.bot, config.DB_PATH)

    if hub_backup.is_configured():
        asyncio.create_task(hub_backup.upload_database_backup(db))

    ud.clear()
    ud["last_saved"] = utils.utc_now_iso()
    return None


# ==================================================================
# إشعار المشرفين ببلاغ جديد
# ==================================================================
