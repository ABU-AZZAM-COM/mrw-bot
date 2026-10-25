"""
معالجات المستخدم العادي: قائمة رئيسية بأزرار مضمنة، استقبال بلاغ عبر المحادثة،
استقبال تفاصيل البلاغ وأدلته الاختيارية، حفظ، وإشعار المشرفين.

مسار المحادثة (ConversationHandler): اختيار القسم والمنصة عند الحاجة، التفاصيل،
مرفق اختياري، رابط/موقع/جهة اتصال بحسب المسار، ثم التأكيد النهائي.

استدعاءات الأزرار المضمنة للمستخدم (prefix usr:):
  usr:menu     → القائمة الرئيسية
  usr:report   → بدء بلاغ جديد (يفتح اختيار القسم)
  usr:status   → حالة بلاغي الأخير
  usr:about    → عن النظام
  usr:contact  → تواصل مع الإدارة
  usr:category:X → اختيار قسم البلاغ
  usr:confirm  → تأكيد إرسال البلاغ
  usr:cancel   → إلغاء العملية
"""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from telegram import ReplyKeyboardRemove, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import admin
import archive
import config
import guards
import image_moderation
import sightengine_moderation
import hub_backup
import texts
import utils
from database import Database
from keyboards import (
    admin_notify_buttons,
    back_to_menu_kb,
    cancel_inline_kb,
    confirm_report_kb,
    main_menu_kb,
    my_reports_kb,
    report_open_kb,
    REPORT_CATEGORIES,
    SOCIAL_PLATFORM_LABELS,
    report_category_kb,
    social_platform_kb,
    optional_step_kb,
    contact_request_kb,
    location_request_kb,
)

logger = logging.getLogger(__name__)

# مراحل المحادثة
ASK_CATEGORY, ASK_PLATFORM, ASK_DESC, ASK_PHOTO, ASK_LINK, ASK_LOCATION, ASK_CONTACT, CONFIRM, ASK_FOLLOWUP, ASK_SUPPORT = range(10)
SUPPORT_MAX_LENGTH = 1000

# نصوص الأزرار (أنماط regex) لإلغاء العملية وتأكيد الحفظ
CANCEL_RE = r"^\s*❌\s*إلغاء\s*$"
CONFIRM_RE = r"^\s*✅\s*إرسال\s+البلاغ\s*$"

# بادئة استدعاءات المستخدم (تعريف أصلي في keyboards — يُستعمل هنا للفحص السريع)
USR_PREFIX = "usr:"

# الحد الأقصى للبطاقات المعروضة في شاشة «بلاغاتي» (تُجلب واحدة زيادة للكشف عن المزيد)
MY_REPORTS_MAX = 6

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


# ==================================================================
# نقطة الدخول: /start أو /report
# ==================================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """القائمة الرئيسية الاحترافية — كل التنقل بأزرار مضمنة، لا أوامر كتابية."""
    user = update.effective_user
    chat = update.effective_chat
    db = _db(context)

    # ------------------------------------------------------------------
    # الحماية ③: الاستقبال في المحادثات الخاصة فقط
    # (يمنع استعمال البوت داخل المجموعات، وهو أقوى وسيلة ضد الإبلاغ الجماعي)
    # ------------------------------------------------------------------
    if not guards.is_private_chat(chat.type):
        await update.effective_message.reply_text(
            texts.PRIVATE_ONLY.format(bot=config.BOT_USERNAME), parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    await db.record_user(user.id, user.username, user.full_name)

    await db.log_event(user.id, "start")

    context.user_data.clear()
    welcome = texts.WELCOME
    if await guards.is_emergency(db):
        welcome = texts.REPORTING_PAUSED + "\n\n" + welcome
    await update.effective_message.reply_text(
        welcome,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )
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
            await msg.reply_text(
                texts.PHOTO_REMINDER, parse_mode=ParseMode.HTML
            )
            return "", None, "", image_moderation.ScreeningResult(False)
        if doc.file_size and doc.file_size > config.MAX_PHOTO_MB * 1024 * 1024:
            await msg.reply_text(
                texts.PHOTO_TOO_BIG.format(mb=config.MAX_PHOTO_MB),
                parse_mode=ParseMode.HTML,
            )
            return "", None, "", image_moderation.ScreeningResult(False)
        ext = mimetypes.guess_extension(doc.mime_type or "") or ".jpg"
        media = doc

    if media.file_size and media.file_size > config.MAX_PHOTO_MB * 1024 * 1024:
        await msg.reply_text(
            texts.PHOTO_TOO_BIG.format(mb=config.MAX_PHOTO_MB),
            parse_mode=ParseMode.HTML,
        )
        return "", None, "", image_moderation.ScreeningResult(False)

    # ------------------------------------------------------------------
    # حفظ الصورة على القرص: اختياري.
    # في الاستضافة السحابية المجانية قد لا يوجد قرص دائم، ونستطيع الاكتفاء
    # بـ file_id لأن تلغرام يحفظ الصورة في خوادمه إلى الأبد — وهي الطريقة
    # التي تجعل الدليل يبقى متاحًا بعد إعادة تشغيل الخادم.
    # ------------------------------------------------------------------
    if config.SAVE_MEDIA_LOCALLY:
        stamp = _stamp_name()
        path = config.MEDIA_DIR / f"{stamp}_{user.id}{ext}"
        try:
            tg_file = await media.get_file()
            await tg_file.download_to_drive(custom_path=str(path))
        except Exception:
            logger.exception("فشل تحميل الصورة للمستخدم %s", user.id)
            await msg.reply_text(texts.PHOTO_FAILED, parse_mode=ParseMode.HTML)
            return "", None, "", image_moderation.ScreeningResult(False)

        if not path.exists() or path.stat().st_size == 0:
            await msg.reply_text(texts.PHOTO_FAILED, parse_mode=ParseMode.HTML)
            return "", None, "", image_moderation.ScreeningResult(False)

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
    ud = context.user_data
    if ud.get("report_category") == "social":
        prompt = await update.effective_message.reply_text(
            texts.ASK_LINK,
            parse_mode=ParseMode.HTML,
            reply_markup=optional_step_kb("link"),
        )
        _remember_optional_prompt(context, prompt)
        return ASK_LINK
    if ud.get("report_category") == "field":
        await update.effective_message.reply_text(
            texts.ASK_LOCATION,
            parse_mode=ParseMode.HTML,
            reply_markup=location_request_kb(),
        )
        return ASK_LOCATION
    return await _ask_contact(update, context)


async def _ask_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
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


async def _clear_optional_prompt(context: ContextTypes.DEFAULT_TYPE) -> None:
    prompt = context.user_data.pop("optional_prompt", None)
    if not prompt:
        return
    try:
        await context.bot.edit_message_reply_markup(
            chat_id=prompt["chat_id"],
            message_id=prompt["message_id"],
            reply_markup=None,
        )
    except BadRequest as exc:
        logger.debug("Optional report prompt is already unavailable: %s", exc)


async def _show_report_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud = context.user_data
    category = REPORT_CATEGORIES.get(ud.get("report_category"), "غير محدد")
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
    await update.effective_message.reply_text(
        "✅ اكتملت البيانات الاختيارية.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await update.effective_message.reply_text(
        texts.SUMMARY.format(
            category=utils.esc(category),
            platform=utils.esc(ud.get("platform") or "—"),
            photo="✅ مستلمة" if ud.get("photo_file_id") else "غير مرفقة",
            link=(
                utils.link_anchor(ud["post_link"])
                if ud.get("post_link")
                else "غير مرفق"
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
    progress = await msg.reply_text("🔄 جارٍ فحص الصورة أمنيًا...")

    photo_path, photo_file_id, photo_name, screening = await _save_photo(
        update, context
    )
    if not photo_file_id:
        await progress.edit_text("⚠️ لم يكتمل استلام الصورة. أعد إرسالها أو تخطَّ المرفق.")
        prompt = await msg.reply_text(
            texts.ASK_PHOTO,
            parse_mode=ParseMode.HTML,
            reply_markup=optional_step_kb("photo"),
        )
        _remember_optional_prompt(context, prompt)
        return ASK_PHOTO
    if screening.flagged:
        await progress.edit_text("⛔ أوقف النظام هذا البلاغ وفق نتيجة فحص الصورة.")
        await _freeze_for_sensitive_image(
            update, context, screening, photo_path, photo_file_id
        )
        return ConversationHandler.END

    await progress.edit_text(
        "⚠️ تعذر إكمال الفحص الآلي بالكامل؛ سيُحال البلاغ للمراجعة اليدوية."
        if screening.review_required
        else "✅ اكتمل الفحص الآلي الأولي للصورة."
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
    prompt = await update.effective_message.reply_text(
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
        prompt = await update.message.reply_text(
            texts.LINK_INVALID,
            parse_mode=ParseMode.HTML,
            reply_markup=optional_step_kb("link"),
        )
        _remember_optional_prompt(context, prompt)
        return ASK_LINK

    progress = None
    if config.VIRUSTOTAL_API_KEY:
        progress = await update.message.reply_text(
            "🔎 جار فحص الرابط عبر VirusTotal..."
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
            else await update.message.reply_text(
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
            else "✅ اكتمل فحص الرابط."
        )
        await progress.edit_text(result_text, parse_mode=ParseMode.HTML)
    context.user_data["post_link"] = text
    return await _ask_contact(update, context)


async def remind_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _clear_optional_prompt(context)
    prompt = await update.effective_message.reply_text(
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
        await update.message.reply_text(
            texts.DESC_TOO_LONG.format(length=len(text), max=config.MAX_DESC_LEN),
            parse_mode=ParseMode.HTML,
        )
        return ASK_DESC

    substance_verdict = guards.check_substance(text)
    if substance_verdict.blocked:
        await update.message.reply_text(
            substance_verdict.message, parse_mode=ParseMode.HTML
        )
        return ASK_DESC
    duplicate = await guards.check_duplicate(_db(context), "", text)
    context.user_data["description"] = text
    context.user_data["needs_review"] = bool(
        duplicate.code in {"exact_duplicate", "similar_duplicate"}
    )
    context.user_data["duplicate_of"] = duplicate.extra.get("duplicate_of")
    prompt = await update.message.reply_text(
        texts.ASK_PHOTO,
        parse_mode=ParseMode.HTML,
        reply_markup=optional_step_kb("photo"),
    )
    _remember_optional_prompt(context, prompt)
    return ASK_PHOTO


async def remind_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        texts.ASK_DESC.format(maxlen=config.MAX_DESC_LEN),
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_inline_kb(),
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
        if place != "⏭️ تخطي":
            if not place or len(place) > 120:
                await message.reply_text("اكتب وصفًا للمكان لا يتجاوز 120 حرفًا، أو اختر تخطي.")
                return ASK_LOCATION
            context.user_data["place"] = place
    return await _ask_contact(update, context)


async def receive_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    contact = message.contact
    if contact:
        if contact.user_id is not None and contact.user_id != update.effective_user.id:
            await message.reply_text(
                "يرجى مشاركة رقمك أنت فقط أو اختيار تخطي.",
                reply_markup=contact_request_kb(),
            )
            return ASK_CONTACT
        context.user_data["contact_phone"] = contact.phone_number[:32]
    elif (message.text or "").strip() != "⏭️ تخطي":
        await message.reply_text(
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
        message = update.effective_message
        if message:
            await message.reply_text(
                texts.REPORT_PAUSED_ALERT,
                parse_mode=ParseMode.HTML,
            )
        return CONFIRM

    # تلاشي أزرار التأكيد فور الضغط عليها (تبقى الرسالة نصًا فقط)
    if update.callback_query:
        try:
            await update.callback_query.edit_message_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001 — قد تغيّر الرسالة أو تكون مجمدة
            logger.debug("تعذر إزالة أزرار التأكيد بعد الضغط")

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
        await update.effective_message.reply_text(
            texts.GENERAL_ERROR, parse_mode=ParseMode.HTML
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
    await update.effective_message.reply_text(
        texts.SAVED.format(
            seq=report["seq"],
            time=utils.fmt_local(report["created_at"]),
            status=texts.badge(report["status"]),
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )

    await _notify_admins(context, report)

    # ------------------------------------------------------------------
    # الأرشفة الرسمية:
    #   1) نشر البلاغ في القناة الرسمية الموحّدة (سجل زمني واحد لكل البلاغات)
    #   2) رفع نسخة من قاعدة البيانات إلى القناة (حماية البيانات من فقدان القرص)
    # الحماية ⑤: النشر لا يتم تلقائيًا — يُنتظر رأي المشرف
    # (يمنع وصول بلاغ كيدي أو مخالف إلى القناة الرسمية).
    # ------------------------------------------------------------------
    if config.AUTO_PUBLISH_TO_ARCHIVE:
        await archive.post_report(context.bot, report)
    await archive.backup_database(context.bot, config.DB_PATH)

    # النسخ إلى مستودع Hugging Face الاحتياطي (الطبقة الأساسية):
    # رفع في خيط خلفي ولا يحجب رد البوت. فشلُه مُسجّل في السجلات ويُعاد
    # تلقائياً مع الرفعة الدورية القادمة.
    if hub_backup.is_configured():
        asyncio.create_task(asyncio.to_thread(hub_backup.upload_backup, config.DB_PATH))

    ud.clear()
    ud["last_saved"] = utils.utc_now_iso()
    return ConversationHandler.END


# ==================================================================
# أمر /app — فتح التطبيق
# ==================================================================
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
    if violation_type:
        if len(violation_type) > 60 or violation_type not in await db.effective_vtypes():
            await update.effective_message.reply_text(
                "⚠️ نوع المخالفة المحدد لم يعد متاحًا. أعد فتح التطبيق واختر تصنيفًا من القائمة.",
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
    await update.effective_message.reply_text(
        texts.APP_DATA_OK.format(
            vtype=utils.esc(violation_type or "غير محدد"),
            place=utils.esc(place or "غير محدد"),
            review=(
                utils.esc(review_notice) + "\n\n"
                if review_notice
                else (
                    "⚠️ سيُحال البلاغ للمراجعة اليدوية بسبب تشابهه مع بلاغ سابق.\n\n"
                    if duplicate and duplicate.code in {"exact_duplicate", "similar_duplicate"}
                    else ""
                )
            ),
        ),
        parse_mode=ParseMode.HTML,
    )
    await update.effective_message.reply_text(
        texts.APP_SEND_PHOTO, parse_mode=ParseMode.HTML
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
        "🔄 جارٍ فحص الصورة أمنيًا..."
    )
    photo_path, photo_file_id, photo_name, screening = await _save_photo(
        update, context
    )
    if not photo_file_id:
        await progress.edit_text("⚠️ لم يكتمل استلام الصورة. أعد إرسالها.")
        await update.effective_message.reply_text(
            texts.PHOTO_FAILED, parse_mode=ParseMode.HTML
        )
        return None
    if screening.flagged:
        await progress.edit_text("⛔ أوقف النظام هذا البلاغ وفق نتيجة فحص الصورة.")
        await _freeze_for_sensitive_image(
            update, context, screening, photo_path, photo_file_id
        )
        return None
    await progress.edit_text(
        "⚠️ تعذر إكمال الفحص الآلي بالكامل؛ سيُحال البلاغ للمراجعة اليدوية."
        if screening.review_required
        else "✅ اكتمل الفحص الآلي الأولي للصورة."
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
        texts.SAVED.format(
            seq=report["seq"],
            time=utils.fmt_local(report["created_at"]),
            status=texts.badge(report["status"]),
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
    await archive.backup_database(context.bot, config.DB_PATH)

    # النسخ إلى مستودع Hugging Face الاحتياطي (الطبقة الأساسية):
    # رفع في خيط خلفي ولا يحجب رد البوت. فشلُه مُسجّل في السجلات ويُعاد
    # تلقائياً مع الرفعة الدورية القادمة.
    if hub_backup.is_configured():
        asyncio.create_task(asyncio.to_thread(hub_backup.upload_backup, config.DB_PATH))

    ud.clear()
    ud["last_saved"] = utils.utc_now_iso()
    return None


# ==================================================================
# إشعار المشرفين ببلاغ جديد
# ==================================================================
async def _notify_admins(context: ContextTypes.DEFAULT_TYPE, report: dict) -> None:
    """إرسال صورة البلاغ مع ملخصها وأزرارها إلى كل مشرف معد في ADMIN_IDS.

    الأزرار تنفّذ أوامر اللوحة نفسها (adm:s / adm:rj / adm:v) — إشعار بقرار فوري
    دون الانتقال بين الشاشات، وكل إجراء يُسجَّل في سجل التدقيق باسم المدير.
    """
    caption = texts.ADMIN_NOTIFY.format(
        seq=report["seq"],
        name=utils.esc(report["full_name"] or "-"),
        username=texts.username_display(report["username"]),
        uid=report["user_id"],
        vtype=utils.esc(report.get("violation_type") or "غير محدد"),
        category=utils.esc(report.get("category") or "—"),
        platform=utils.esc(report.get("platform") or "—"),
        place=utils.esc(
            report.get("place")
            or (
                f"{report['latitude']:.5f}, {report['longitude']:.5f}"
                if report.get("latitude") is not None and report.get("longitude") is not None
                else "—"
            )
        ),
        contact=utils.esc(report.get("contact_phone") or "—"),
        time=utils.fmt_local(report["created_at"]),
        anchor=utils.link_anchor(report["post_link"]) if report.get("post_link") else "—",
        desc=utils.esc(utils.trunc(report["description"], 300)),
    )
    moderation_note = str(report.get("admin_note") or "").strip()
    image_review_hold = bool(
        report.get("needs_review") and moderation_note
    )
    if image_review_hold:
        caption += "\n\n⚠️ <b>يتطلب مراجعة يدوية:</b> " + utils.esc(moderation_note)
    markup = admin_notify_buttons(report["id"])
    photo = Path(report["photo_path"]) if report["photo_path"] else None
    file_id = report.get("photo_file_id")

    for admin_id in config.ADMIN_IDS:
        try:
            if photo is not None and photo.exists():
                with open(photo, "rb") as fp:
                    await context.bot.send_photo(
                        chat_id=admin_id, photo=fp, caption=caption,
                        parse_mode=ParseMode.HTML, reply_markup=markup,
                    )
            elif file_id:
                # لا نسخة محلية على القرص — نرسل الصورة من تخزين تلغرام مباشرة
                await context.bot.send_photo(
                    chat_id=admin_id, photo=file_id, caption=caption,
                    parse_mode=ParseMode.HTML, reply_markup=markup,
                )
            else:
                await context.bot.send_message(
                    chat_id=admin_id, text=caption, parse_mode=ParseMode.HTML,
                    reply_markup=markup,
                )
        except Exception:
            logger.warning("تعذر إشعار المشرف %s ببلاغ %s", admin_id, report["seq"])


# ==================================================================
# أمر /help — الدليل السريع
# ==================================================================
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دليل الاستخدام المختصر — يعمل في أي لحظة (حتى أثناء محادثة نشطة)."""
    await update.effective_message.reply_text(
        texts.HELP,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )


# ==================================================================
# أمر /status — نفس زر «📊 حالة بلاغي» في رسالة مباشرة
# ==================================================================
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بطاقات «بلاغاتي» بأمر كتابي — لمن يخطر له ذلك دون فتح القائمة."""
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
            await message.reply_text("الاستخدام: /reply رقم_البلاغ المعلومات الإضافية")
        return
    if chat is None or chat.type != "private":
        await message.reply_text("أرسل المعلومات الإضافية في المحادثة الخاصة مع البوت.")
        return

    report = await _db(context).get_by_seq(context.args[0].strip())
    if (
        not report
        or int(report.get("user_id") or 0) != user.id
        or not report.get("followup_requested_at")
    ):
        await message.reply_text("لا يوجد طلب معلومات مفتوح لهذا البلاغ.")
        return

    response = " ".join(context.args[1:]).strip()
    if not 2 <= len(response) <= 2000:
        await message.reply_text("يجب أن تكون المعلومات بين حرفين و2000 حرف.")
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
                text=(
                    f"📩 معلومات إضافية للبلاغ {report.get('seq') or report['id']}\n"
                    f"المبلّغ: {user.full_name} ({user.id})\n\n{response}"
                ),
            )
            delivered += 1
        except Exception:
            logger.warning(
                "تعذر إيصال معلومات متابعة البلاغ %s للمشرف %s",
                report["id"], admin_id,
            )
    if delivered:
        await message.reply_text("تم استلام المعلومات وإيصالها إلى الإدارة.")
    else:
        await message.reply_text("تم حفظ المعلومات، لكن تعذر إيصالها للمشرفين حاليًا.")


async def receive_support_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Persist a reporter's support message and notify configured admins."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not context.user_data.get("support_pending"):
        if message:
            await message.reply_text(texts.WELCOME_HINT, parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    text = (message.text or "").strip()
    if not 2 <= len(text) <= SUPPORT_MAX_LENGTH:
        await message.reply_text(
            texts.SUPPORT_TOO_LONG
            if len(text) > SUPPORT_MAX_LENGTH
            else "اكتب رسالة من حرفين على الأقل، أو اختر إلغاء.",
            parse_mode=ParseMode.HTML,
        )
        return ASK_SUPPORT
    if not config.ADMIN_IDS:
        context.user_data.pop("support_pending", None)
        await message.reply_text(
            texts.SUPPORT_UNAVAILABLE,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )
        return ConversationHandler.END

    db = _db(context)
    await db.record_user(user.id, user.username, user.full_name)
    await db.open_user_conversation(user.id)
    await db.add_user_message(user.id, "in", text)
    await db.audit(
        user.id,
        "user_support_message",
        str(user.id),
        f"عدد الأحرف: {len(text)}",
    )
    context.user_data.pop("support_pending", None)

    delivered = 0
    admin_message = (
        "📩 <b>رسالة جديدة إلى الإدارة</b>\n"
        "────────────────────\n"
        f"👤 المرسل: {utils.esc(user.full_name or '-')}\n"
        f"🆔 المعرف: <code>{user.id}</code>\n"
        f"🔗 الحساب: @{utils.esc(user.username or 'بدون معرف')}\n\n"
        f"{utils.esc(text)}\n\n"
        "يمكن متابعة المحادثة من قسم الرسائل في لوحة الإدارة."
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=admin_message,
                parse_mode=ParseMode.HTML,
            )
            delivered += 1
        except Exception:
            logger.warning(
                "تعذر تنبيه المشرف %s برسالة المستخدم %s",
                admin_id,
                user.id,
                exc_info=True,
            )

    await message.reply_text(
        texts.SUPPORT_RECEIVED if delivered else texts.SUPPORT_SAVED_NO_NOTIFY,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )
    return ConversationHandler.END


async def receive_followup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    response = (message.text or "").strip()
    rid = context.user_data.get("followup_report_id")
    if not isinstance(rid, int) or not 2 <= len(response) <= 2000:
        await message.reply_text("يجب أن تكون المعلومات بين حرفين و2000 حرف.")
        return ASK_FOLLOWUP
    db = _db(context)
    report = await db.get_by_id(rid)
    if (
        not report
        or int(report.get("user_id") or 0) != user.id
        or not report.get("followup_requested_at")
        or report.get("followup_response")
    ):
        context.user_data.pop("followup_report_id", None)
        await message.reply_text("لم يعد هناك طلب معلومات مفتوح لهذا البلاغ.")
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
                text=(
                    f"📩 معلومات إضافية للبلاغ {report.get('seq') or rid}\n"
                    f"المبلّغ: {utils.esc(user.full_name or '-')} ({user.id})\n\n"
                    f"{utils.esc(response)}"
                ),
                parse_mode=ParseMode.HTML,
            )
            delivered += 1
        except Exception:
            logger.warning("تعذر إيصال متابعة البلاغ %s للمشرف %s", rid, admin_id)
    context.user_data.pop("followup_report_id", None)
    await message.reply_text(
        "تم استلام المعلومات وإيصالها إلى الإدارة."
        if delivered
        else "تم حفظ المعلومات، لكن تعذر إيصالها للمشرفين حاليًا."
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
    await update.effective_message.reply_text(texts.CANCELLED, parse_mode=ParseMode.HTML)
    context.user_data.clear()
    if update.effective_user:
        _pending_app_users.discard(update.effective_user.id)
    return ConversationHandler.END


# ==================================================================
# معالج استدعاءات الأزرار المضمنة للمستخدم (usr:*)
# ==================================================================
def _my_reports_text(reports: list, total: int) -> str:
    """نص شاشة «بلاغاتي»: بطاقة مختصرة لكل بلاغ (رقمها وحالتها وزمنها)."""
    shown = reports[:MY_REPORTS_MAX]
    lines = [
        f"▣ <b><code>{r['seq']}</code></b> — {texts.badge(r['status'])}\n"
        f"🕒 {utils.fmt_local(r['created_at'])}\n"
        for r in shown
    ]
    more = texts.MY_REPORTS_MORE if total > len(shown) else ""
    return texts.MY_REPORTS_HEADER.format(
        count=total, lines="".join(lines) + more
    )


async def on_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    """يعالج كل ضغطة على زر مخصص للمستخدم في القائمة الرئيسية أو أثناء الإبلاغ."""
    query = update.callback_query
    db = _db(context)
    data = query.data or ""

    if data == f"{USR_PREFIX}contact" and any(
        key in context.user_data
        for key in ("report_category", "app_draft", "followup_report_id")
    ):
        try:
            await query.answer(texts.SUPPORT_DURING_REPORT, show_alert=True)
        except (BadRequest, Forbidden):
            logger.debug("Could not alert user while a report draft is active")
        return

    if data == f"{USR_PREFIX}confirm" and await guards.is_emergency(db):
        try:
            await query.answer(texts.REPORT_PAUSED_ALERT, show_alert=True)
        except (BadRequest, Forbidden):
            logger.debug("Could not show emergency notice for report confirmation")
        return CONFIRM

    # حارس العتيق: استدعاءات قد تكون أُجيبت سابقًا أو فاتتها لوحات قديمة
    try:
        await query.answer()
    except (BadRequest, Forbidden) as exc:
        logger.debug("استدعاء عتيق أو مجاب مسبقًا (تم تجاهل الإجابة): %s", exc)

    if not data.startswith(USR_PREFIX):
        return
    parts = data.split(":")

    # --- القائمة الرئيسية ---
    if parts[1] == "menu":
        welcome = texts.WELCOME
        if await guards.is_emergency(db):
            welcome = texts.REPORTING_PAUSED + "\n\n" + welcome
        await query.edit_message_text(
            welcome,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )
        return ConversationHandler.END

    # --- بلاغ جديد ---
    if parts[1] == "report":
        if await guards.is_emergency(db):
            await query.edit_message_text(
                texts.REPORTING_PAUSED,
                parse_mode=ParseMode.HTML,
                reply_markup=back_to_menu_kb(),
            )
            return ConversationHandler.END
        verdict = await guards.check_rate_limits(db, query.from_user.id)
        if verdict.blocked:
            await query.edit_message_text(
                verdict.message, parse_mode=ParseMode.HTML,
                reply_markup=back_to_menu_kb(),
            )
            return
        daily = await guards.check_daily_limit(db, query.from_user.id)
        if daily.blocked:
            await query.edit_message_text(
                daily.message, parse_mode=ParseMode.HTML,
                reply_markup=back_to_menu_kb(),
            )
            return

        await db.log_event(query.from_user.id, "report")
        context.user_data.clear()
        # اختيار أحد مسارات البلاغ الأربعة قبل إدخال التفاصيل.
        await query.edit_message_text(
            "اختر القسم الأقرب إلى موضوع بلاغك:",
            parse_mode=ParseMode.HTML,
            reply_markup=report_category_kb(),
        )
        return ASK_CATEGORY

    # --- فتح التطبيق ---
    # أُزيل زر فتح التطبيق من الأزرار المضمنة (قرار الواجهة الاحترافية)؛
    # التطبيق يظل متاحًا عبر أمر /app فقط إن وُجد رابطه.
    if parts[1] == "app":
        await query.answer(texts.APP_NOT_READY, show_alert=False)
        return

    # --- بلاغاتي: بطاقات كل بلاغات المتبلّغ (بدل آخر بلاغ فقط) ---
    if parts[1] == "status":
        reports, total = await db.list_user_reports(
            query.from_user.id, limit=MY_REPORTS_MAX + 1
        )
        if not reports:
            await query.edit_message_text(
                texts.NO_REPORTS_YET, parse_mode=ParseMode.HTML,
                reply_markup=back_to_menu_kb(),
            )
            return ConversationHandler.END
        await query.edit_message_text(
            _my_reports_text(reports, total),
            parse_mode=ParseMode.HTML,
            reply_markup=my_reports_kb(reports[:MY_REPORTS_MAX]),
        )
        return ConversationHandler.END

    # --- بطاقة تفصيل بلاغ من «بلاغاتي» (usr:r:<id>) ---
    if parts[1] == "r" and len(parts) > 2 and parts[2].isdigit():
        rid = int(parts[2])
        r = await db.get_by_id(rid)
        # حماية: لا يفتح المتبلّغ بطاقة بلاغٍ ليس له
        if not r or int(r.get("user_id") or 0) != query.from_user.id:
            await query.answer(texts.REPORT_NOT_FOUND, show_alert=True)
            return ConversationHandler.END
        await query.edit_message_text(
            texts.MY_REPORT_OPEN.format(
                seq=r["seq"],
                time=utils.fmt_local(r["created_at"]),
                status=texts.badge(r["status"]),
                status_detail=texts.status_explanation(r["status"]),
                desc=utils.esc(utils.trunc(r.get("description") or "-", 200)),
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=report_open_kb(
                rid, bool(r.get("followup_requested_at") and not r.get("followup_response"))
            ),
        )
        return ConversationHandler.END

    if parts[1] == "followup" and len(parts) == 3 and parts[2].isdigit():
        rid = int(parts[2])
        report = await db.get_by_id(rid)
        if (
            not report
            or int(report.get("user_id") or 0) != query.from_user.id
            or not report.get("followup_requested_at")
            or report.get("followup_response")
        ):
            await query.answer("لا يوجد طلب معلومات مفتوح لهذا البلاغ.", show_alert=True)
            return ConversationHandler.END
        context.user_data["followup_report_id"] = rid
        await query.edit_message_text(
            texts.ASK_FOLLOWUP,
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_inline_kb(),
        )
        return ASK_FOLLOWUP

    # --- عن النظام ---
    if parts[1] == "about":
        await query.edit_message_text(
            texts.ABOUT_SYSTEM, parse_mode=ParseMode.HTML,
            reply_markup=back_to_menu_kb(),
        )
        return ConversationHandler.END

    # --- تواصل مع الإدارة ---
    if parts[1] == "contact":
        await query.edit_message_text(
            texts.ASK_SUPPORT,
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_inline_kb(),
        )
        context.user_data["support_pending"] = True
        return ASK_SUPPORT

    if parts[1] == "category" and len(parts) == 3:
        category = parts[2]
        if category not in REPORT_CATEGORIES:
            await query.answer("هذا القسم غير معروف.", show_alert=True)
            return ASK_CATEGORY
        context.user_data.clear()
        context.user_data.update(
            report_category=category,
            category=REPORT_CATEGORIES[category],
            post_link="",
            photo_path="",
            photo_file_id=None,
            place="",
            latitude=None,
            longitude=None,
            contact_phone="",
        )
        intro = texts.CATEGORY_INTROS[category]
        if category == "social":
            await query.edit_message_text(
                intro + "\n\n" + texts.PLATFORM_PROMPT,
                parse_mode=ParseMode.HTML,
                reply_markup=social_platform_kb(),
            )
            return ASK_PLATFORM
        await query.edit_message_text(
            intro + "\n\n" + texts.ASK_DESC.format(maxlen=config.MAX_DESC_LEN),
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_inline_kb(),
        )
        return ASK_DESC

    if parts[1] == "platform" and len(parts) == 3:
        platform_key = parts[2]
        if platform_key not in SOCIAL_PLATFORM_LABELS:
            await query.answer("المنصة غير معروفة.", show_alert=True)
            return ASK_PLATFORM
        context.user_data["platform"] = SOCIAL_PLATFORM_LABELS[platform_key]
        context.user_data["platform_key"] = platform_key
        await query.edit_message_text(
            texts.CATEGORY_INTROS["social"]
            + "\n\n"
            + texts.ASK_DESC.format(maxlen=config.MAX_DESC_LEN),
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_inline_kb(),
        )
        return ASK_DESC

    if parts[1] == "skip" and len(parts) == 3:
        step = parts[2]
        if step == "photo":
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except BadRequest:
                pass
            context.user_data.pop("optional_prompt", None)
            return await _advance_after_evidence(update, context)
        if step == "link" and context.user_data.get("report_category") == "social":
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except BadRequest:
                pass
            context.user_data.pop("optional_prompt", None)
            try:
                await _assess_link_and_description(context, db, "")
            except ValueError as exc:
                await query.answer(str(exc), show_alert=True)
                return ASK_LINK
            context.user_data["post_link"] = ""
            return await _ask_contact(update, context)
        if step == "location" and context.user_data.get("report_category") == "field":
            return await _ask_contact(update, context)
        if step == "contact":
            return await _show_report_confirmation(update, context)

    # --- اختيار نوع المخالفة ---
    if parts[1] == "type" and len(parts) > 2:
        vtype = ":".join(parts[2:])
        context.user_data["violation_type"] = vtype
        await query.edit_message_text(
            texts.ASK_DESC.format(maxlen=config.MAX_DESC_LEN),
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_inline_kb(),
        )
        return ASK_DESC

    # --- تأكيد الإرسال ---
    if parts[1] == "confirm":
        draft = context.user_data.get("app_draft")
        if draft:
            return await confirm_save_from_draft(update, context, db)
        # التدفق العادي (محادثة الصورة/الرابط/الوصف) — تحميل الحفظ النهائي
        return await confirm_save(update, context)

    # --- إلغاء ---
    if parts[1] == "cancel":
        context.user_data.clear()
        _pending_app_users.discard(query.from_user.id)
        await query.edit_message_text(
            texts.CANCELLED, parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )
        return ConversationHandler.END

    logger.warning("استدعاء مستخدم غير معروف: %s", data)
    # نبّه المستخدم بهدوء (قد تكون الأزرار من قائمة قديمة)
    try:
        await query.answer(texts.UNKNOWN_BUTTON, show_alert=True)
    except Exception:  # noqa: BLE001 — إجابة ثانية لنفس الاستدعاء متوقعة
        pass


async def confirm_save_from_draft(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database) -> None:
    """حفظ بلاغ من مسودة التطبيق بعد الضغط على زر تأكيد الإرسال."""
    query = update.callback_query
    user = query.from_user
    ud = context.user_data
    draft = ud.pop("app_draft", None)
    _pending_app_users.discard(user.id)

    if not draft:
        await query.answer("لا توجد مسودة صالحة", show_alert=True)
        return

    # نطلب الصورة لأن التطبيق لا يستطيع رفع الصور مباشرة عبر web_app_data
    ud["app_draft"] = draft  # إعادة المسودة إلى السياق حتى يقرأها معالج الصورة
    _pending_app_users.add(user.id)
    await query.edit_message_text(
        texts.APP_SEND_PHOTO, parse_mode=ParseMode.HTML
    )


# ==================================================================
# رسائل خارج المحادثة / خارج السياق المتوقع
# ==================================================================
async def welcome_hint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أي رسالة خاصة عندما لا تكون المحادثة نشطة (لا يوجد /start)."""
    user = update.effective_user
    message = update.effective_message
    chat = update.effective_chat
    if (
        user
        and message
        and message.text
        and chat
        and guards.is_private_chat(chat.type)
    ):
        db = _db(context)
        if await db.user_conversation_open(user.id):
            text = message.text.strip()[:config.MAX_DESC_LEN]
            if text:
                await db.record_user(user.id, user.username, user.full_name)
                await db.add_user_message(user.id, "in", text)
                delivered = 0
                admin_message = (
                    "📩 <b>رسالة جديدة في محادثة مفتوحة</b>\n"
                    f"👤 {utils.esc(user.full_name or '-')} "
                    f"(<code>{user.id}</code>)\n\n"
                    f"{utils.esc(text)}"
                )
                for admin_id in config.ADMIN_IDS:
                    try:
                        await context.bot.send_message(
                            chat_id=admin_id,
                            text=admin_message,
                            parse_mode=ParseMode.HTML,
                        )
                        delivered += 1
                    except Exception:
                        logger.warning(
                            "تعذر تنبيه المشرف %s برسالة المستخدم %s",
                            admin_id,
                            user.id,
                            exc_info=True,
                        )
                await message.reply_text(
                    "✅ وصلت رسالتك إلى الإدارة."
                    if delivered
                    else "✅ حُفظت رسالتك في سجل المحادثة، لكن تعذّر تنبيه الإدارة الآن."
                )
                return
    await update.effective_message.reply_text(texts.WELCOME_HINT, parse_mode=ParseMode.HTML)


async def unmatched(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رسالة غير مفهومة داخل المحادثة — تبقى المحادثة في حالتها الحالية."""
    await update.effective_message.reply_text(texts.FALLBACK_HINT, parse_mode=ParseMode.HTML)
    return None


async def exit_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فتح لوحة المشرف حتى أثناء محادثة البلاغ (تُنهي المحادثة)."""
    await admin.cmd_admin(update, context)
    return ConversationHandler.END


# ==================================================================
# معالج الأخطاء العام
# ==================================================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالج الأخطاء العام: سجل كامل هنا، وباقة لطيفة للمستخدم دون تفاصيل تقنية."""
    err = context.error

    # تعديل رسالة إلى نفس المحتوى (أزرار مُتلاشية سابقاً) — ضجيج متوقّع لا يستحق إنذارًا
    if isinstance(err, BadRequest) and "not modified" in str(err).lower():
        logger.debug("تعديل رسالة مُتكرّر بلا تغيير — تجاهل: %s", err)
        return

    logger.error("خطأ أثناء معالجة تحديث: %s", err, exc_info=err)

    # لا رسائل للمستخدم إن لم نعد في محادثة، أو حجب البوت (الرد سيفشل حتماً)
    if not isinstance(update, Update) or update.effective_message is None:
        return
    if isinstance(err, Forbidden):
        logger.info("المستخدم %s حجب البوت — لم يُرسل إشعار الخطأ",
                    update.effective_user.id if update.effective_user else "?")
        return
    try:
        await update.effective_message.reply_text(
            texts.GENERAL_ERROR, parse_mode=ParseMode.HTML
        )
    except Exception:  # noqa: BLE001 — فشل الرد لا يهدم من يصلح نفسه
        pass


# ==================================================================
# بناء ConversationHandler وتسليمه إلى main.py
# ==================================================================
# ==================================================================
# أدوات مساعدة
# ==================================================================
def _stamp_name() -> str:
    """اسم فريد لملف الصورة: سنة-شهر-يوم_ساعة-دقيقة-ثانية"""
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def build_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler(["start", "report"], cmd_start),
            # القائمة الرئيسية تعمل بأزرار مضمنة من أول رسالة
            CallbackQueryHandler(on_user_callback, pattern=r"^usr:"),
        ],
        states={
            ASK_CATEGORY: [
                MessageHandler(filters.Regex(CANCEL_RE), cmd_cancel),
                MessageHandler(filters.ALL & ~filters.COMMAND, unmatched),
            ],
            ASK_PLATFORM: [
                MessageHandler(filters.Regex(CANCEL_RE), cmd_cancel),
                MessageHandler(filters.ALL & ~filters.COMMAND, unmatched),
            ],
            ASK_DESC: [
                MessageHandler(filters.Regex(CANCEL_RE), cmd_cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_desc),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, remind_desc),
            ],
            ASK_PHOTO: [
                MessageHandler(filters.Regex(CANCEL_RE), cmd_cancel),
                MessageHandler(filters.PHOTO | filters.Document.IMAGE, receive_photo),
                MessageHandler(
                    ~filters.PHOTO & ~filters.Document.IMAGE & ~filters.COMMAND,
                    remind_photo,
                ),
            ],
            ASK_LINK: [
                MessageHandler(filters.Regex(CANCEL_RE), cmd_cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_link),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, remind_link),
            ],
            ASK_LOCATION: [
                MessageHandler(filters.Regex(CANCEL_RE), cmd_cancel),
                MessageHandler(
                    (filters.LOCATION | filters.TEXT) & ~filters.COMMAND,
                    receive_location,
                ),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, unmatched),
            ],
            ASK_CONTACT: [
                MessageHandler(filters.Regex(CANCEL_RE), cmd_cancel),
                MessageHandler(
                    (filters.CONTACT | filters.TEXT) & ~filters.COMMAND,
                    receive_contact,
                ),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, unmatched),
            ],
            CONFIRM: [
                MessageHandler(filters.Regex(CONFIRM_RE), confirm_save),
                MessageHandler(filters.Regex(CANCEL_RE), cmd_cancel),
                MessageHandler(~filters.Regex(CONFIRM_RE), unmatched),
            ],
            ASK_FOLLOWUP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_followup),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, unmatched),
            ],
            ASK_SUPPORT: [
                MessageHandler(filters.Regex(CANCEL_RE), cmd_cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_support_message),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, unmatched),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CommandHandler("reply", cmd_reply),
            CommandHandler(["start", "report"], cmd_start),
            CommandHandler(["admin", "reports"], exit_to_admin),
            CommandHandler("app", cmd_app),
            # الأزرار المضمنة للمستخدم تعمل حتى داخل المحادثة النشطة
            CallbackQueryHandler(on_user_callback, pattern=r"^usr:"),
            # ملاحظة: نستثني الأوامر من المصيدة الشاملة حتى لا تبتلع
            # أوامر المشرف (/panic /resume /audit) والأوامر الأخرى.
            MessageHandler(filters.ALL & ~filters.COMMAND, unmatched),
        ],
        allow_reentry=True,
        per_user=True,
        per_chat=True,
    )
