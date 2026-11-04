"""تدفق إرسال البلاغ.

نُقلت الدوال من ``bot/handlers.py`` مع إعادة تصدير أسمائها هناك.
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
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes, ConversationHandler, filters

from bot import control, keyboards, texts
from bot import helpers as _helpers
from bot.helpers import (
    ACTIVE_PROMPT_KEY,
    ASK_CATEGORY,
    ASK_CONTACT,
    ASK_DESC,
    ASK_LINK,
    ASK_LOCATION,
    ASK_PHOTO,
    ASK_PLATFORM,
    ASK_TYPE,
    CONFIRM,
    _clear_user_prompt,
    _clear_welcome_photo,
    _contact_back_step,
    _db,
    _delete_user_message,
    _main_menu_content,
    _pending_app_users,
    _remember_user_prompt,
    _send_clean_prompt,
    _with_verified_reporter_badge,
)
from bot.keyboards import (
    back_to_menu_kb,
    cancel_inline_kb,
    confirm_report_kb,
    contact_request_kb,
    location_request_kb,
    main_menu_kb,
    optional_step_kb,
    report_category_kb,
    report_type_kb,
    social_platform_kb,
)
from app import config
from database import Database
from services import (
    archive,
    guards,
    hub_backup,
    image_moderation,
    sightengine_moderation,
    utils,
)
from services.report_taxonomy import REPORT_CATEGORIES

logger = logging.getLogger(__name__)


def _late_bound(name: str):
    """غلاف يحل الاسم من ``bot.handlers`` وقت الطلب لا وقت التركيب."""

    def call(*args, **kwargs):
        from bot import handlers

        return getattr(handlers, name)(*args, **kwargs)

    return call


_discard_verification_capture = _late_bound("_discard_verification_capture")
_notify_admins = _late_bound("_notify_admins")
_stamp_name = _late_bound("_stamp_name")
_privacy_policy_url = _late_bound("_privacy_policy_url")


async def _dispatch_show_step(*args, **kwargs):
    """استدعاء ``_show_report_step`` عبر ``bot.handlers`` وقت الطلب.

    يبقي الدالة قابلة للاستبدال في الاختبارات (patch.object على handlers)
    ويعيد استخدام نفس التنفيذ المعاد تصديره عندما لا يكون هناك استبدال.
    """
    from bot import handlers

    return await handlers._show_report_step(*args, **kwargs)


async def _show_report_step(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    step: int,
) -> int:
    ud = context.user_data
    if step == ASK_CATEGORY:
        text, markup = (
            texts.INLINE_MESSAGES["report_category_prompt"],
            report_category_kb(),
        )
        privacy_url = await _privacy_policy_url(_db(context))
        if privacy_url:
            text += texts.format_inline_message(
                "privacy_report_link",
                url=utils.esc(privacy_url),
            )
    elif step == ASK_TYPE:
        category = ud.get("report_category_key")
        types = (await _db(context).effective_vtype_groups()).get(category, [])
        text = texts.INLINE_MESSAGES["report_type_prompt"]
        markup = report_type_kb(types)
    elif step == ASK_PLATFORM:
        text = texts.CATEGORY_INTROS["social"] + "\n\n" + texts.PLATFORM_PROMPT
        markup = social_platform_kb()
    elif step == ASK_DESC:
        category = ud.get("report_category")
        intro = texts.CATEGORY_INTROS.get(category, "")
        text = (
            (intro + "\n\n" if intro else "")
            + texts.ASK_DESC.format(maxlen=config.MAX_DESC_LEN)
        )
        back_to = "platform" if category == "social" else "category"
        markup = cancel_inline_kb(back_to)
    elif step == ASK_PHOTO:
        text, markup = texts.ASK_PHOTO, optional_step_kb("photo")
    elif step == ASK_LINK:
        text, markup = texts.ASK_LINK, optional_step_kb("link")
    elif step == ASK_LOCATION:
        text, markup = texts.ASK_LOCATION, location_request_kb()
    elif step == ASK_CONTACT:
        text, markup = texts.ASK_CONTACT, contact_request_kb()
    else:
        raise ValueError(f"Unknown report step: {step}")
    ud["report_state"] = step
    await _send_clean_prompt(
        update,
        context,
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
    )
    return step


async def _start_report_flow(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database
) -> int:
    if await guards.is_emergency(db):
        await _send_clean_prompt(
            update,
            context,
            texts.REPORTING_PAUSED,
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_menu_kb(),
        )
        return ConversationHandler.END

    user = update.effective_user
    if user is None:
        return ConversationHandler.END
    verdict = await guards.check_rate_limits(db, user.id)
    if verdict.blocked:
        await _send_clean_prompt(
            update,
            context,
            verdict.message,
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_menu_kb(),
        )
        return ConversationHandler.END
    daily = await guards.check_daily_limit(db, user.id)
    if daily.blocked:
        await _send_clean_prompt(
            update,
            context,
            daily.message,
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_menu_kb(),
        )
        return ConversationHandler.END

    await db.log_event(user.id, "report")
    active_prompt = context.user_data.get(ACTIVE_PROMPT_KEY)
    context.user_data.clear()
    if active_prompt:
        context.user_data[ACTIVE_PROMPT_KEY] = active_prompt
    return await _dispatch_show_step(update, context, ASK_CATEGORY)


# ==================================================================
# نقطة الدخول: /start أو /report
# ==================================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """القائمة الرئيسية الاحترافية — كل التنقل بأزرار مضمنة، لا أوامر كتابية."""
    user = update.effective_user
    chat = update.effective_chat
    db = _db(context)

    # ------------------------------------------------------------------
    # الحماية ③: استقبال البلاغات في المحادثات الخاصة فقط.
    # يمنع استخدام البوت داخل المجموعات والبلاغ الجماعي الكيدي.
    # ------------------------------------------------------------------
    if not guards.is_private_chat(chat.type):
        await update.effective_message.reply_text(
            texts.PRIVATE_ONLY.format(bot=config.BOT_USERNAME), parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    command = (update.effective_message.text or "").split(maxsplit=1)
    command = command[0].split("@", 1)[0].lower() if command else ""
    await db.record_user(user.id, user.username, user.full_name)

    if command == "/start":
        await db.log_event(user.id, "start")

    await _discard_verification_capture(context, user.id)
    await _clear_welcome_photo(context)
    active_prompt = context.user_data.get(ACTIVE_PROMPT_KEY)
    context.user_data.clear()
    if active_prompt:
        context.user_data[ACTIVE_PROMPT_KEY] = active_prompt
    _pending_app_users.discard(user.id)
    if command == "/report":
        state = await _start_report_flow(update, context, db)
        await _delete_user_message(update, context)
        return state

    welcome, parse_mode = await _main_menu_content(db, user.id)
    welcome_photo = context.bot_data.get("welcome_photo")
    if command == "/start" and isinstance(welcome_photo, dict) and isinstance(
        welcome_photo.get("data"), bytes
    ):
        try:
            photo_message = await update.effective_message.reply_photo(
                photo=welcome_photo["data"]
            )
            context.user_data["_welcome_photo_prompt"] = {
                "chat_id": photo_message.chat_id,
                "message_id": photo_message.message_id,
            }
        except Exception:
            logger.exception("تعذر إرسال صورة الترحيب للمستخدم %s", user.id)
    if command == "/start":
        await _send_clean_prompt(
            update,
            context,
            welcome,
            parse_mode=parse_mode,
            reply_markup=keyboards.welcome_continue_kb(),
        )
        await _delete_user_message(update, context)
        return ConversationHandler.END
    await _send_clean_prompt(
        update,
        context,
        welcome,
        parse_mode=parse_mode,
        reply_markup=main_menu_kb(update.effective_user.id if update.effective_user else None),
    )
    await _delete_user_message(update, context)
    return ConversationHandler.END


# ==================================================================
# الحالة 1: الصورة التوثيقية
# ==================================================================
async def _save_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save the photo and optionally run local screening before it reaches admins."""
    msg = update.message
    user = update.effective_user

    if msg.photo:
        media = msg.photo[-1]          # أعلى جودة متاحة
        ext = ".jpg"
    else:
        doc = msg.document
        if not (doc.mime_type or "").lower().startswith("image/"):
            return "", None, "", image_moderation.ScreeningResult(
                False, error=texts.PHOTO_REMINDER
            )
        if doc.file_size and doc.file_size > config.MAX_PHOTO_MB * 1024 * 1024:
            return "", None, "", image_moderation.ScreeningResult(
                False,
                error=texts.PHOTO_TOO_BIG.format(mb=config.MAX_PHOTO_MB),
            )
        ext = mimetypes.guess_extension(doc.mime_type or "") or ".jpg"
        media = doc

    if media.file_size and media.file_size > config.MAX_PHOTO_MB * 1024 * 1024:
        return "", None, "", image_moderation.ScreeningResult(
            False,
            error=texts.PHOTO_TOO_BIG.format(mb=config.MAX_PHOTO_MB),
        )

    # ------------------------------------------------------------------
    # حفظ الصورة على القرص اختياري؛ يمكن الاعتماد على file_id في الاستضافة السحابية.
    # يحتفظ Telegram بالصورة، فتظل متاحة بعد إعادة تشغيل الخادم.
    # ------------------------------------------------------------------
    if config.SAVE_MEDIA_LOCALLY:
        stamp = _stamp_name()
        path = config.MEDIA_DIR / f"{stamp}_{user.id}{ext}"
        try:
            tg_file = await media.get_file()
            await tg_file.download_to_drive(custom_path=str(path))
        except Exception:
            logger.exception("فشل تحميل الصورة للمستخدم %s", user.id)
            return "", None, "", image_moderation.ScreeningResult(
                False, error=texts.PHOTO_FAILED
            )

        if not path.exists() or path.stat().st_size == 0:
            return "", None, "", image_moderation.ScreeningResult(
                False, error=texts.PHOTO_FAILED
            )

        photo_path = str(path)
        photo_name = path.name
    else:
        photo_path = ""
        photo_name = media.file_id

    context.user_data.pop("image_moderation_flag", None)
    screening = await _screen_uploaded_photo(
        media, photo_path=photo_path, extension=ext, user_id=user.id
    )
    if screening.review_required:
        context.user_data["image_moderation_flag"] = screening.error or (
            "مؤشرات محتوى حساس: "
            + ", ".join(screening.labels)
            + f" (ثقة {screening.max_score:.0%})"
        )
    if screening.flagged and photo_path:
        try:
            Path(photo_path).unlink(missing_ok=True)
        except OSError:
            logger.exception("تعذر حذف صورة المخالفة للمستخدم %s", user.id)
        photo_path = ""
        photo_name = media.file_id
    return photo_path, media.file_id, photo_name, screening


async def _advance_after_evidence(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Continue the category-specific optional fields after the image step."""
    category = context.user_data.get("report_category")
    if category == "social":
        return await _dispatch_show_step(update, context, ASK_LINK)
    if category == "field":
        return await _dispatch_show_step(update, context, ASK_LOCATION)
    return await _ask_contact(update, context)


async def _ask_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["report_state"] = ASK_CONTACT
    await _send_clean_prompt(
        update,
        context,
        texts.ASK_CONTACT,
        parse_mode=ParseMode.HTML,
        reply_markup=contact_request_kb(),
    )
    return ASK_CONTACT


def _remember_optional_prompt(context: ContextTypes.DEFAULT_TYPE, message) -> None:
    context.user_data["optional_prompt"] = {
        "chat_id": message.chat_id,
        "message_id": message.message_id,
    }
    _remember_user_prompt(context, message)


async def _clear_optional_prompt(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("optional_prompt", None)
    await _clear_user_prompt(context)


async def _show_report_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud = context.user_data
    category = REPORT_CATEGORIES.get(
        ud.get("report_category"), texts.INLINE_MESSAGES["unspecified_value"]
    )
    place = ud.get("place") or ""
    if not place and ud.get("latitude") is not None and ud.get("longitude") is not None:
        place = f"{ud['latitude']:.5f}, {ud['longitude']:.5f}"
    review_note = ""
    if ud.get("needs_review") and ud.get("link_review_reason") != "virustotal":
        review_note = texts.DUPLICATE_REVIEW_NOTE
    if ud.get("link_review_reason") == "virustotal":
        review_note += texts.VIRUSTOTAL_REVIEW
    if ud.get("image_moderation_flag"):
        review_note += "⚠️ صورة البلاغ تحتاج إلى مراجعة يدوية.\n"
    await _send_clean_prompt(
        update,
        context,
        texts.SUMMARY.format(
            category=utils.esc(category),
            platform=utils.esc(ud.get("platform") or "—"),
            photo=(
                texts.INLINE_MESSAGES["report_photo_received"]
                if ud.get("photo_file_id")
                else texts.INLINE_MESSAGES["report_photo_missing"]
            ),
            link=(
                utils.link_anchor(ud["post_link"])
                if ud.get("post_link")
                else texts.INLINE_MESSAGES["report_link_missing"]
            ),
            place=utils.esc(place or "—"),
            contact=utils.esc(ud.get("contact_phone") or "—"),
            vtype=utils.esc(ud.get("violation_type") or category),
            desc=utils.esc(ud.get("description") or ""),
            review_note=review_note,
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=confirm_report_kb(),
    )
    context.user_data["report_state"] = CONFIRM
    return CONFIRM


async def _assess_link_and_description(
    context: ContextTypes.DEFAULT_TYPE, db: Database, link: str
) -> None:
    ud = context.user_data
    if (
        not link
        and ud.get("report_category") == "social"
        and not ud.get("photo_file_id")
    ):
        raise ValueError(
            "لقسم مخالفات النشر، أرفق رابط المنشور أو لقطة شاشة واحدة على الأقل."
        )
    description = ud.get("description") or ""
    duplicate = None
    if link:
        verdict, substance, duplicate = await guards.inspect_report_content(
            db, link, description
        )
        if verdict.blocked:
            raise ValueError(verdict.message)
        if substance is not None and substance.blocked:
            raise ValueError(substance.message)
        if verdict.extra.get("needs_review"):
            ud["needs_review"] = True
            ud["link_review_reason"] = verdict.extra.get("review_reason")
        detected_platform = verdict.extra.get("platform") or ""
        selected_platform = ud.get("platform") or ""
        platform_key = ud.get("platform_key")
        matches = (
            not detected_platform
            or platform_key == "other"
            or (platform_key == "instagram_tiktok" and detected_platform in {"إنستغرام", "تيك توك"})
            or detected_platform == selected_platform
        )
        if detected_platform and selected_platform and not matches:
            raise ValueError(
                "الرابط لا يتوافق مع المنصة المختارة. أرسل رابطًا من المنصة نفسها "
                "أو اختر «منصة أخرى» عند بدء البلاغ."
            )
        elif detected_platform:
            ud["platform"] = detected_platform
        elif verdict.code == "unknown_platform":
            ud["needs_review"] = True
    else:
        duplicate = await guards.check_duplicate(db, "", description)
    if duplicate and duplicate.code in {"exact_duplicate", "similar_duplicate"}:
        ud["needs_review"] = True
        ud["duplicate_of"] = duplicate.extra.get("duplicate_of")


async def _screen_uploaded_photo(media, photo_path: str, extension: str, user_id: int):
    """Screen private complaint uploads locally and with Sightengine when configured."""
    temporary_path: Path | None = None
    source_path = Path(photo_path) if photo_path else None
    local_result = image_moderation.ScreeningResult(False)
    sightengine_scores = None
    sightengine_error = ""
    try:
        if source_path is None:
            with tempfile.NamedTemporaryFile(
                prefix="mrw-screen-", suffix=extension, delete=False
            ) as handle:
                temporary_path = Path(handle.name)
            tg_file = await media.get_file()
            await tg_file.download_to_drive(custom_path=str(temporary_path))
            source_path = temporary_path

        if not source_path.is_file() or source_path.stat().st_size == 0:
            raise RuntimeError("downloaded image is empty")

        if image_moderation.is_enabled():
            try:
                local_result = await asyncio.to_thread(
                    image_moderation.screen_image, source_path
                )
            except Exception:
                logger.exception(
                    "تعذر فحص صورة المستخدم %s محليًا", user_id
                )
                local_result = image_moderation.ScreeningResult(
                    False,
                    review_required=True,
                    error="تعذر إكمال الفحص المحلي؛ يلزم التحقق اليدوي",
                )

        if sightengine_moderation.credentials():
            try:
                sightengine_scores = await sightengine_moderation.inspect_image(
                    source_path
                )
            except sightengine_moderation.SightengineError as exc:
                logger.warning(
                    "تعذر فحص صورة المستخدم %s عبر Sightengine: %s",
                    user_id,
                    exc,
                )
                sightengine_error = (
                    f"تعذر فحص الصورة عبر Sightengine: {exc}؛ يلزم التحقق اليدوي"
                )
        else:
            sightengine_error = (
                "بيانات Sightengine غير مكتملة؛ تم الاعتماد على الفحص المحلي"
            )

        labels = set(local_result.labels)
        max_score = local_result.max_score
        sightengine_flagged = False
        if sightengine_scores is not None:
            max_score = max(max_score, sightengine_scores.max_score)
            sightengine_flagged = sightengine_scores.is_restricted
            labels.update(
                "SIGHTENGINE_" + class_name.upper().replace(".", "_")
                for class_name, score in sightengine_scores.scores
                if score >= sightengine_moderation.RESTRICTION_THRESHOLD
            )

        return image_moderation.ScreeningResult(
            flagged=local_result.flagged or sightengine_flagged,
            review_required=(
                local_result.review_required
                or bool(labels)
                or bool(sightengine_error)
            ),
            labels=tuple(sorted(labels)),
            max_score=max_score,
            error=sightengine_error or local_result.error,
        )
    except Exception:
        logger.exception("تعذر فحص صورة المستخدم %s؛ أُحيل البلاغ للمراجعة اليدوية", user_id)
        return image_moderation.ScreeningResult(
            False,
            review_required=True,
            error="تعذر إكمال الفحص الآلي للصورة؛ يلزم التحقق اليدوي",
        )
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("تعذر حذف ملف الفحص المؤقت للمستخدم %s", user_id, exc_info=True)


async def _freeze_for_sensitive_image(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    screening: image_moderation.ScreeningResult,
    photo_path: str = "",
    photo_file_id: str | None = None,
) -> None:
    """Persist the block and its evidence without forwarding media in Telegram."""
    user = update.effective_user
    db = _db(context)
    reason = (
        "كشف آلي لمؤشرات محتوى حساس: "
        + ", ".join(screening.labels)
        + f" (ثقة {screening.max_score:.0%})"
    )
    frozen_until = "9999-12-31T00:00:00"
    previous_block = await db.get_block(user.id)
    await db.save_block(
        user.id,
        int(previous_block.get("strikes") or 0),
        frozen_until,
        reason,
        evidence_photo_path=photo_path,
        evidence_photo_file_id=photo_file_id,
    )
    try:
        await db.audit(
            None,
            "automated_sensitive_image_freeze",
            str(user.id),
            reason,
        )
    except Exception:
        logger.exception("تعذر تسجيل تجميد الصورة في سجل التدقيق للمستخدم %s", user.id)

    _pending_app_users.discard(user.id)
    context.user_data.clear()
    admin_message = (
        "🚨 <b>تجميد آلي دائم — محتوى حساس</b>\n"
        f"المستخدم: {utils.esc(user.full_name or '-')}\n"
        f"المعرّف: <code>{user.id}</code>\n"
        f"المؤشرات: <code>{utils.esc(', '.join(screening.labels))}</code>\n"
        f"ثقة المصنّف: <b>{screening.max_score:.0%}</b>\n"
        "لم تُرسل الصورة أو تُرفق بالتنبيه. هذا تصنيف آلي احتمالي؛ "
        "يمكن مراجعة السبب ورفع الحجب يدويًا من لوحة الإدارة."
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=admin_message,
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            logger.exception(
                "تعذر تنبيه المشرف %s عن التجميد الآلي للمستخدم %s",
                admin_id,
                user.id,
            )

    try:
        notice = await guards.render_block_message(
            db,
            "manual_block",
            {"kind": "حظر دائم", "reason": "إرسال صورة خالفت قواعد الاستخدام"},
        )
        await update.effective_message.reply_text(notice, parse_mode=ParseMode.HTML)
    except Exception:
        logger.exception("تعذر إبلاغ المستخدم %s بالحظر الآلي", user.id)


async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استقبال الصورة (مضغوطة أو كملف) وحفظها في مجلد media/."""
    msg = update.message
    await _clear_optional_prompt(context)
    progress = await _send_clean_prompt(
        update, context, texts.INLINE_MESSAGES["photo_check_progress"]
    )

    photo_path, photo_file_id, photo_name, screening = await _save_photo(
        update, context
    )
    if not photo_file_id:
        prompt = await _send_clean_prompt(
            update,
            context,
            (screening.error or texts.INLINE_MESSAGES["photo_receipt_incomplete"])
            + "\n\n"
            + texts.ASK_PHOTO,
            parse_mode=ParseMode.HTML,
            reply_markup=optional_step_kb("photo"),
        )
        _remember_optional_prompt(context, prompt)
        return ASK_PHOTO
    if screening.flagged:
        await progress.edit_text(texts.INLINE_MESSAGES["photo_check_interrupted"])
        await _clear_user_prompt(context)
        await _freeze_for_sensitive_image(
            update, context, screening, photo_path, photo_file_id
        )
        return ConversationHandler.END

    await progress.edit_text(
        texts.INLINE_MESSAGES["photo_check_review_required"]
        if screening.review_required
        else texts.INLINE_MESSAGES["photo_check_passed"]
    )
    context.user_data.update(
        photo_path=photo_path,
        photo_file_id=photo_file_id,
        photo_name=photo_name,
    )
    return await _advance_after_evidence(update, context)


async def remind_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عندما يرسل المستخدم شيئًا غير صورة في خطوة الصورة."""
    await _clear_optional_prompt(context)
    prompt = await _send_clean_prompt(
        update,
        context,
        texts.ASK_PHOTO,
        parse_mode=ParseMode.HTML,
        reply_markup=optional_step_kb("photo"),
    )
    _remember_optional_prompt(context, prompt)
    return ASK_PHOTO


# ==================================================================
# الحالة 2: رابط المنشور
# ==================================================================
async def receive_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    await _clear_optional_prompt(context)
    if not utils.is_valid_url(text):
        prompt = await _send_clean_prompt(
            update,
            context,
            texts.LINK_INVALID,
            parse_mode=ParseMode.HTML,
            reply_markup=optional_step_kb("link"),
        )
        _remember_optional_prompt(context, prompt)
        return ASK_LINK

    progress = None
    if config.VIRUSTOTAL_API_KEY:
        progress = await _send_clean_prompt(
            update, context, texts.INLINE_MESSAGES["link_check_progress"]
        )
    try:
        await _assess_link_and_description(context, _db(context), text)
    except ValueError as exc:
        prompt = (
            await progress.edit_text(
                str(exc),
                parse_mode=ParseMode.HTML,
                reply_markup=optional_step_kb("link"),
            )
            if progress
            else await _send_clean_prompt(
                update,
                context,
                str(exc),
                parse_mode=ParseMode.HTML,
                reply_markup=optional_step_kb("link"),
            )
        )
        _remember_optional_prompt(context, prompt)
        return ASK_LINK
    if progress:
        result_text = (
            texts.VIRUSTOTAL_REVIEW
            if context.user_data.get("link_review_reason") == "virustotal"
            else texts.INLINE_MESSAGES["link_check_complete"]
        )
        await progress.edit_text(result_text, parse_mode=ParseMode.HTML)
    context.user_data["post_link"] = text
    return await _ask_contact(update, context)


async def remind_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _clear_optional_prompt(context)
    prompt = await _send_clean_prompt(
        update,
        context,
        texts.ASK_LINK,
        parse_mode=ParseMode.HTML,
        reply_markup=optional_step_kb("link"),
    )
    _remember_optional_prompt(context, prompt)
    return ASK_LINK


# ==================================================================
# الحالة 3: وصف المخالفة
# ==================================================================
async def receive_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if len(text) > config.MAX_DESC_LEN:
        await _send_clean_prompt(
            update,
            context,
            texts.DESC_TOO_LONG.format(length=len(text), max=config.MAX_DESC_LEN)
            + "\n\n"
            + texts.ASK_DESC.format(maxlen=config.MAX_DESC_LEN),
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_inline_kb(
                "platform"
                if context.user_data.get("report_category") == "social"
                else "category"
            ),
        )
        return ASK_DESC

    substance_verdict = guards.check_substance(text)
    if substance_verdict.blocked:
        await _send_clean_prompt(
            update,
            context,
            substance_verdict.message
            + "\n\n"
            + texts.ASK_DESC.format(maxlen=config.MAX_DESC_LEN),
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_inline_kb(
                "platform"
                if context.user_data.get("report_category") == "social"
                else "category"
            ),
        )
        return ASK_DESC
    duplicate = await guards.check_duplicate(_db(context), "", text)
    context.user_data["description"] = text
    context.user_data["needs_review"] = bool(
        duplicate.code in {"exact_duplicate", "similar_duplicate"}
    )
    context.user_data["duplicate_of"] = duplicate.extra.get("duplicate_of")
    context.user_data["report_state"] = ASK_PHOTO
    prompt = await _send_clean_prompt(
        update,
        context,
        texts.ASK_PHOTO,
        parse_mode=ParseMode.HTML,
        reply_markup=optional_step_kb("photo"),
    )
    _remember_optional_prompt(context, prompt)
    return ASK_PHOTO


async def remind_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_clean_prompt(
        update,
        context,
        texts.ASK_DESC.format(maxlen=config.MAX_DESC_LEN),
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_inline_kb(
            "platform"
            if context.user_data.get("report_category") == "social"
            else "category"
        ),
    )
    return ASK_DESC


async def receive_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message.location:
        context.user_data["latitude"] = message.location.latitude
        context.user_data["longitude"] = message.location.longitude
        context.user_data["place"] = ""
    else:
        place = (message.text or "").strip()
        if place == control.semantic_text("back", "↩️ رجوع"):
            return await _dispatch_show_step(update, context, ASK_PHOTO)
        if place != control.semantic_text("skip", "⏭️ تخطي"):
            if not place or len(place) > 120:
                await _send_clean_prompt(
                    update,
                    context,
                    texts.INLINE_MESSAGES["location_description_too_long"],
                    reply_markup=location_request_kb(),
                )
                return ASK_LOCATION
            context.user_data["place"] = place
        else:
            context.user_data["place"] = ""
            context.user_data["latitude"] = None
            context.user_data["longitude"] = None
    return await _ask_contact(update, context)


async def receive_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    contact = message.contact
    if not contact and (message.text or "").strip() == control.semantic_text(
        "back", "↩️ رجوع"
    ):
        return await _dispatch_show_step(
            update, context, _contact_back_step(context)
        )
    if contact:
        if contact.user_id is not None and contact.user_id != update.effective_user.id:
            await _send_clean_prompt(
                update,
                context,
                texts.INLINE_MESSAGES["contact_must_be_own"],
                reply_markup=contact_request_kb(),
            )
            return ASK_CONTACT
        context.user_data["contact_phone"] = contact.phone_number[:32]
    elif (message.text or "").strip() == control.semantic_text(
        "skip", "⏭️ تخطي"
    ):
        context.user_data["contact_phone"] = ""
    else:
        await _send_clean_prompt(
            update,
            context,
            texts.ASK_CONTACT,
            parse_mode=ParseMode.HTML,
            reply_markup=contact_request_kb(),
        )
        return ASK_CONTACT
    return await _show_report_confirmation(update, context)


# ==================================================================
# الحالة 4: التأكيد والحفظ
# ==================================================================
async def confirm_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حفظ البلاغ نهائيًا وإشعار المشرفين بوجوده."""
    db = _db(context)
    if await guards.is_emergency(db):
        await _send_clean_prompt(
            update,
            context,
            texts.REPORT_PAUSED_ALERT,
            parse_mode=ParseMode.HTML,
            reply_markup=confirm_report_kb(),
        )
        return CONFIRM

    user = update.effective_user
    ud = context.user_data
    try:
        report = await db.add_report(
            user_id=user.id,
            username=user.username,
            full_name=user.full_name,
            photo_path=ud.get("photo_path", ""),
            photo_file_id=ud.get("photo_file_id"),
            post_link=ud.get("post_link", ""),
            description=ud["description"],
            violation_type=ud.get("violation_type") or ud.get("category"),
            category=ud.get("category"),
            platform=ud.get("platform"),
            place=ud.get("place") or None,
            latitude=ud.get("latitude"),
            longitude=ud.get("longitude"),
            contact_phone=ud.get("contact_phone") or None,
            needs_review=1 if ud.get("needs_review") or ud.get("image_moderation_flag") else 0,
            admin_note=ud.get("image_moderation_flag") or None,
        )
    except Exception:
        logger.exception("فشل حفظ البلاغ للمستخدم %s", user.id)
        await _send_clean_prompt(
            update,
            context,
            texts.GENERAL_ERROR,
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    logger.info("بلاغ جديد %s من المستخدم %s", report["seq"], user.id)
    try:
        await db.add_fingerprint(
            report["id"],
            guards.link_hash(ud["post_link"]) if ud.get("post_link") else None,
            guards.text_hash(ud["description"]),
            None,
        )
    except Exception:
        logger.exception("تعذرت إضافة بصمة البلاغ %s", report["seq"])
    saved_text = await _with_verified_reporter_badge(
        texts.SAVED.format(
            seq=report["seq"],
            time=utils.fmt_local(report["created_at"]),
            status=texts.badge(report["status"]),
        ),
        db,
        user.id,
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            saved_text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboards.saved_receipt_kb(),
        )
    else:
        await _clear_user_prompt(context)
        await update.effective_message.reply_text(
            saved_text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboards.saved_receipt_kb(),
        )

    await _notify_admins(context, report)

    # ------------------------------------------------------------------
    # الأرشفة الرسمية:
    # 1) نشر البلاغ في القناة الموحدة كسجل زمني.
    # 2) رفع نسخة من قاعدة البيانات لحمايتها من فقدان القرص.
    # لا يُنشر البلاغ تلقائيًا قبل مراجعة المشرف.
    # ------------------------------------------------------------------
    if config.AUTO_PUBLISH_TO_ARCHIVE:
        await archive.post_report(context.bot, report)
    if config.DB_BACKEND == "sqlite":
        await archive.backup_database(context.bot, config.DB_PATH)

    if hub_backup.is_configured():
        asyncio.create_task(hub_backup.upload_database_backup(db))

    ud.clear()
    ud["last_saved"] = utils.utc_now_iso()
    return ConversationHandler.END


# ==================================================================
# أمر /app — فتح التطبيق
# ==================================================================
