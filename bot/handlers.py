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
import hashlib
import json
import logging
import mimetypes
import secrets
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telegram import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app import config
from bot import admin, keyboards, texts
from services import (
    archive,
    guards,
    hub_backup,
    identity_image_checks,
    image_moderation,
    sightengine_moderation,
    utils,
    verified_reporters,
    word_filter,
)
from database import Database, STATUS_AWAITING_INFO, STATUS_REJECTED
from bot.keyboards import (
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
    return texts.WELCOME, ParseMode.HTML


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
    text = texts.VERIFICATION_CONSENT_DEFAULT
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
        await context.bot.edit_message_reply_markup(
            chat_id=message.chat_id,
            message_id=message.message_id,
            reply_markup=reply_markup,
        )
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


async def _show_report_step(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    step: int,
) -> int:
    ud = context.user_data
    if step == ASK_CATEGORY:
        text, markup = "اختر القسم الأقرب إلى موضوع بلاغك:", report_category_kb()
        privacy_url = await _privacy_policy_url(_db(context))
        if privacy_url:
            text += (
                "\n\n<a href=\""
                + utils.esc(privacy_url)
                + "\">اطّلع على سياسة الخصوصية قبل إرسال البلاغ.</a>"
            )
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

    await _discard_verification_capture(context, user.id)
    active_prompt = context.user_data.get(ACTIVE_PROMPT_KEY)
    context.user_data.clear()
    if active_prompt:
        context.user_data[ACTIVE_PROMPT_KEY] = active_prompt
    _pending_app_users.discard(user.id)
    welcome, parse_mode = await _welcome_content(db)
    if await guards.is_emergency(db):
        if parse_mode == ParseMode.HTML:
            welcome = texts.REPORTING_PAUSED + "\n\n" + welcome
        else:
            welcome = "🛑 استقبال البلاغات الجديدة متوقف مؤقتًا.\n\n" + welcome
    command = (update.effective_message.text or "").split(maxsplit=1)
    command = command[0].split("@", 1)[0].lower() if command else ""
    welcome_photo = context.bot_data.get("welcome_photo")
    if command == "/start" and isinstance(welcome_photo, dict) and isinstance(
        welcome_photo.get("data"), bytes
    ):
        try:
            await update.effective_message.reply_photo(photo=welcome_photo["data"])
        except Exception:
            logger.exception("تعذر إرسال صورة الترحيب للمستخدم %s", user.id)
    await _send_clean_prompt(
        update,
        context,
        welcome,
        parse_mode=parse_mode,
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
        return await _show_report_step(update, context, ASK_LINK)
    if category == "field":
        return await _show_report_step(update, context, ASK_LOCATION)
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
    await _send_clean_prompt(
        update,
        context,
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
        update, context, "🔄 جارٍ فحص الصورة أمنيًا..."
    )

    photo_path, photo_file_id, photo_name, screening = await _save_photo(
        update, context
    )
    if not photo_file_id:
        prompt = await _send_clean_prompt(
            update,
            context,
            (screening.error or "⚠️ لم يكتمل استلام الصورة.")
            + "\n\n"
            + texts.ASK_PHOTO,
            parse_mode=ParseMode.HTML,
            reply_markup=optional_step_kb("photo"),
        )
        _remember_optional_prompt(context, prompt)
        return ASK_PHOTO
    if screening.flagged:
        await progress.edit_text("⛔ أوقف النظام هذا البلاغ وفق نتيجة فحص الصورة.")
        await _clear_user_prompt(context)
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
            update, context, "🔎 جار فحص الرابط عبر VirusTotal..."
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
            else "✅ اكتمل فحص الرابط."
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
        if place == "↩️ رجوع":
            return await _show_report_step(update, context, ASK_PHOTO)
        if place != "⏭️ تخطي":
            if not place or len(place) > 120:
                await _send_clean_prompt(
                    update,
                    context,
                    "اكتب وصفًا للمكان لا يتجاوز 120 حرفًا، أو اختر تخطي.",
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
    if not contact and (message.text or "").strip() == "↩️ رجوع":
        return await _show_report_step(
            update, context, _contact_back_step(context)
        )
    if contact:
        if contact.user_id is not None and contact.user_id != update.effective_user.id:
            await _send_clean_prompt(
                update,
                context,
                "يرجى مشاركة رقمك أنت فقط أو اختيار تخطي.",
                reply_markup=contact_request_kb(),
            )
            return ASK_CONTACT
        context.user_data["contact_phone"] = contact.phone_number[:32]
    elif (message.text or "").strip() == "⏭️ تخطي":
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
    saved_text = texts.SAVED.format(
        seq=report["seq"],
        time=utils.fmt_local(report["created_at"]),
        status=texts.badge(report["status"]),
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
    #   1) نشر البلاغ في القناة الرسمية الموحّدة (سجل زمني واحد لكل البلاغات)
    #   2) رفع نسخة من قاعدة البيانات إلى القناة (حماية البيانات من فقدان القرص)
    # الحماية ⑤: النشر لا يتم تلقائيًا — يُنتظر رأي المشرف
    # (يمنع وصول بلاغ كيدي أو مخالف إلى القناة الرسمية).
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
    verification = await context.bot_data["db"].verification_status(
        int(report["user_id"])
    )
    if verification and verification.get("status") == "verified":
        caption += (
            "\n\n🪪 <b>هوية المراسل معتمدة إداريًا</b> — "
            "لا يعني ذلك اعتماد مضمون البلاغ."
        )
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
        or report.get("status") != STATUS_AWAITING_INFO
        or (
            report.get("followup_due_at")
            and report["followup_due_at"]
            <= datetime.now(timezone.utc).isoformat(timespec="seconds")
        )
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

    media_type = None
    file_id = None
    filename = None
    if getattr(message, "photo", None):
        media_type = "photo"
        file_id = message.photo[-1].file_id
    elif getattr(message, "video", None):
        media_type = "video"
        file_id = message.video.file_id
        filename = message.video.file_name
    elif getattr(message, "voice", None):
        media_type = "voice"
        file_id = message.voice.file_id
        filename = "voice.ogg"
    elif getattr(message, "audio", None):
        media_type = "audio"
        file_id = message.audio.file_id
        filename = message.audio.file_name
    elif getattr(message, "document", None):
        media_type = "document"
        file_id = message.document.file_id
        filename = message.document.file_name
    text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    word_count = len(text.split())
    if word_count > SUPPORT_MAX_WORDS:
        await _db(context).log_event(user.id, "support_message")
        await _send_clean_prompt(
            update,
            context,
            "رسالة التواصل لا تتجاوز 100 كلمة. اختصرها وأرسلها مجددًا.",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_inline_kb(),
        )
        return ASK_SUPPORT
    if media_type is None and not 2 <= len(text) <= SUPPORT_MAX_LENGTH:
        await _db(context).log_event(user.id, "support_message")
        await _send_clean_prompt(
            update,
            context,
            texts.SUPPORT_TOO_LONG
            if len(text) > SUPPORT_MAX_LENGTH
            else "اكتب رسالة من حرفين على الأقل، أو اختر إلغاء.",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_inline_kb(),
        )
        return ASK_SUPPORT
    db = _db(context)
    now = datetime.now(timezone.utc)
    ten_minute_count = await db.count_events(
        (now - timedelta(minutes=10)).isoformat(timespec="seconds"),
        user.id,
        "support_message",
    )
    daily_count = await db.count_events(
        (now - timedelta(days=1)).isoformat(timespec="seconds"),
        user.id,
        "support_message",
    )
    if (
        ten_minute_count >= SUPPORT_MAX_PER_10_MINUTES
        or daily_count >= SUPPORT_MAX_PER_DAY
    ):
        await _send_clean_prompt(
            update,
            context,
            "وصلت إلى حد رسائل التواصل. أعد المحاولة بعد انتهاء المهلة؛ "
            "هذا لا يمنعك من استخدام بقية خدمات البوت.",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_inline_kb(),
        )
        return ASK_SUPPORT
    raw_wordlist = await db.get_state("support_blocklist")
    try:
        saved_wordlist = json.loads(raw_wordlist) if raw_wordlist else {"words": []}
        words = saved_wordlist.get("words") if isinstance(saved_wordlist, dict) else None
        if not isinstance(words, list) or any(
            not isinstance(word, str) for word in words
        ):
            raise ValueError("invalid_support_blocklist")
    except (TypeError, ValueError):
        logger.exception("تعذّر تحميل قائمة كلمات التواصل")
        await db.log_event(user.id, "support_message")
        await _send_clean_prompt(
            update,
            context,
            "تعذّر التحقق من إعدادات التواصل الآمنة. لم تُرسل رسالتك؛ "
            "أعد المحاولة لاحقًا أو استخدم البلاغات.",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_inline_kb(),
        )
        return ASK_SUPPORT
    if text and word_filter.contains_blocked_term(text, words):
        await db.log_event(user.id, "support_message")
        await _send_clean_prompt(
            update,
            context,
            "لم تُرسل الرسالة لأنها تحتوي على كلمة تمنعها قائمة الفلترة. "
            "أعد صياغتها أو اختر إلغاء.",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_inline_kb(),
        )
        return ASK_SUPPORT
    if not config.ADMIN_IDS:
        context.user_data.pop("support_pending", None)
        await _clear_user_prompt(context)
        prompt = await message.reply_text(
            texts.SUPPORT_UNAVAILABLE,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )
        _remember_user_prompt(context, prompt)
        return ConversationHandler.END

    await _clear_user_prompt(context)
    await db.record_user(user.id, user.username, user.full_name)
    await db.open_user_conversation(user.id)
    await db.add_user_message(
        user.id,
        "in",
        text or f"[وسيط وارد: {media_type}]",
        media_type=media_type,
        telegram_file_id=file_id,
        telegram_message_id=getattr(message, "message_id", None),
        filename=filename,
    )
    await db.audit(
        user.id,
        "user_support_message",
        str(user.id),
        (
            f"type={media_type}; caption_length={len(text)}"
            if media_type else f"عدد الأحرف: {len(text)}"
        ),
    )
    await db.log_event(user.id, "support_message")
    context.user_data.pop("support_pending", None)

    delivered = 0
    admin_message = (
        "📩 <b>رسالة جديدة إلى الإدارة</b>\n"
        "────────────────────\n"
        f"👤 المرسل: {utils.esc(user.full_name or '-')}\n"
        f"🆔 المعرف: <code>{user.id}</code>\n"
        f"🔗 الحساب: @{utils.esc(user.username or 'بدون معرف')}\n\n"
        +
        (
            f"{utils.esc(text)}\n\n"
            if text else f"أرسل المستخدم وسيطًا: {media_type}\n\n"
        ) + "يمكن متابعة المحادثة من قسم الرسائل في لوحة الإدارة."
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

    prompt = await message.reply_text(
        texts.SUPPORT_RECEIVED if delivered else texts.SUPPORT_SAVED_NO_NOTIFY,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )
    _remember_user_prompt(context, prompt)
    context.user_data.pop("support_pending", None)
    return ConversationHandler.END


async def receive_verification_phone(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat or chat.type != "private":
        return ConversationHandler.END
    if not context.user_data.get("verification_flow"):
        await _send_clean_prompt(
            update,
            context,
            texts.WELCOME_HINT,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )
        return ConversationHandler.END
    if not verified_reporters.is_configured():
        await _send_clean_prompt(
            update,
            context,
            "ميزة التوثيق غير مفعّلة حاليًا لأن إعداد التشفير الآمن لم يكتمل.",
            reply_markup=main_menu_kb(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    contact = message.contact
    if not contact or contact.user_id != user.id:
        await _send_clean_prompt(
            update,
            context,
            "للمتابعة شارك رقم هاتفك أنت عبر زر المشاركة، أو ألغِ العملية. لا ترسل رقم شخص آخر.",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboards.verification_contact_kb(),
        )
        return ASK_VERIFICATION_PHONE

    try:
        normalized_phone = identity_image_checks.normalize_syrian_phone(
            contact.phone_number
        )
    except ValueError:
        await _send_clean_prompt(
            update,
            context,
            "نقبل أرقام الهاتف السورية فقط. أعد مشاركة رقمك السوري من تيليجرام.",
            reply_markup=keyboards.verification_contact_kb(),
        )
        return ASK_VERIFICATION_PHONE

    ticket_hash = verified_reporters.hash_ticket(secrets.token_urlsafe(32))
    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(minutes=VERIFICATION_TICKET_MINUTES)
    ).isoformat(timespec="seconds")
    try:
        request_id = await _db(context).create_verification_ticket(
            user.id,
            ticket_hash,
            verified_reporters.encrypt_phone(normalized_phone),
            expires_at,
            context.user_data.get("verification_consent_at"),
            context.user_data.get("verification_consent_version"),
        )
    except verified_reporters.VerificationUnavailable:
        logger.exception("Identity verification encryption is unavailable")
        await _send_clean_prompt(
            update,
            context,
            "تعذّر بدء التوثيق لأن خدمة الحفظ الآمن غير جاهزة. لم يُحفظ رقم الهاتف.",
            reply_markup=main_menu_kb(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    if request_id is None:
        previous = await _db(context).verification_status(user.id)
        if previous and previous.get("status") == "verified":
            message_text = "حسابك موثّق بالفعل. لا حاجة لإرسال وثائقك مرة أخرى."
        elif previous and previous.get("status") == "pending":
            message_text = (
                "طلب التوثيق قيد المراجعة. لا ترسل صور الهوية مرة أخرى؛ "
                "سيظهر القرار في «حالة توثيق الحساب»."
            )
        else:
            message_text = "يوجد طلب توثيق جارٍ. أعد فتحه من محادثة البوت."
        context.user_data.clear()
        await _send_clean_prompt(
            update,
            context,
            message_text,
            reply_markup=main_menu_kb(),
        )
        return ConversationHandler.END

    context.user_data[VERIFICATION_TICKET_KEY] = ticket_hash
    context.user_data[VERIFICATION_CAPTURES_KEY] = {}
    context.user_data[VERIFICATION_STEP_KEY] = "selfie"
    await _send_clean_prompt(
        update,
        context,
        "تم استلام رقمك السوري. أرسل الآن صورة وجه واضحة كملف من قائمة الإرفاق، "
        "وليس كصورة مضغوطة. لا يمكن للبوت التأكد من أن الصورة التُقطت بالكاميرا "
        "أو أنها حيّة؛ سيجري فحص محلي أولي ثم يراجع المشرف الطلب يدويًا.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ASK_VERIFICATION_SELFIE


async def _discard_verification_capture(
    context: ContextTypes.DEFAULT_TYPE, user_id: int | None
) -> None:
    ticket_hash = context.user_data.get(VERIFICATION_TICKET_KEY)
    context.user_data.pop(VERIFICATION_TICKET_KEY, None)
    context.user_data.pop(VERIFICATION_CAPTURES_KEY, None)
    context.user_data.pop(VERIFICATION_STEP_KEY, None)
    if user_id and isinstance(ticket_hash, str):
        await _db(context).cancel_verification_capture(user_id, ticket_hash)


def _verification_image_error_message(code: str) -> str:
    messages = {
        "image_size_invalid": "حجم الملف غير مناسب. أرسل صورة أصغر من 10 ميغابايت.",
        "image_format_invalid": "أرسل ملف صورة بصيغة JPEG أو PNG.",
        "image_dimensions_invalid": "دقة الصورة منخفضة. أعد الإرسال بصورة أكبر وأوضح.",
        "image_blurry": "الصورة غير واضحة بما يكفي. أعد تصويرها بإضاءة جيدة وثبّت الهاتف.",
        "image_unreadable": "تعذّر فتح الصورة. أعد إرسالها كملف صورة أصلي.",
        "identity_markers_missing": (
            "لم يتمكن الفحص المحلي من قراءة علامات كافية على هذه الجهة من الهوية. "
            "أعد إرسال صورة كاملة وواضحة للجهة المطلوبة."
        ),
        "face_not_found": "لم يُعثر على وجه واضح في الصورة. أعد إرسال صورة شخصية يظهر فيها وجه واحد.",
        "face_multiple": "يجب أن يظهر وجه واحد فقط. أعد إرسال صورة شخصية منفردة.",
        "face_unclear": "الوجه صغير أو غير واضح. أعد إرسال صورة أقرب وأوضح.",
        "inspection_unavailable": (
            "الفحص المحلي غير متاح حاليًا، لذلك لم تُقبل الصورة ولم تُحفظ. "
            "حاول مرة أخرى لاحقًا."
        ),
    }
    return messages.get(
        code,
        "لم تجتز الصورة الفحص الأولي. أعد إرسال صورة واضحة للخطوة الحالية.",
    )


def _verification_state_for_kind(kind: str) -> int:
    return {
        "selfie": ASK_VERIFICATION_SELFIE,
        "id_front": ASK_VERIFICATION_ID_FRONT,
        "id_back": ASK_VERIFICATION_ID_BACK,
    }[kind]


async def _receive_verification_image(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    kind: str,
    next_state: int | None,
) -> int:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat or chat.type != "private":
        return ConversationHandler.END

    ticket_hash = context.user_data.get(VERIFICATION_TICKET_KEY)
    captures = context.user_data.get(VERIFICATION_CAPTURES_KEY)
    if not isinstance(ticket_hash, str) or not isinstance(captures, dict):
        await _send_clean_prompt(
            update,
            context,
            "انتهت جلسة التوثيق. ابدأ طلبًا جديدًا من القائمة الرئيسية.",
            reply_markup=main_menu_kb(),
        )
        return ConversationHandler.END

    document = message.document
    if document is None:
        await message.reply_text(
            "أرسل الصورة كملف من قائمة الإرفاق، وليس كصورة مضغوطة. "
            "يمكنك الإلغاء في أي وقت.",
        )
        return _verification_state_for_kind(kind)
    if document.file_size is not None and (
        document.file_size <= 0
        or document.file_size > identity_image_checks.MAX_IMAGE_BYTES
    ):
        await message.reply_text(_verification_image_error_message("image_size_invalid"))
        return _verification_state_for_kind(kind)
    if document.mime_type not in {"image/jpeg", "image/png"}:
        await message.reply_text(_verification_image_error_message("image_format_invalid"))
        return _verification_state_for_kind(kind)

    try:
        telegram_file = await context.bot.get_file(document.file_id)
        buffer = bytearray()
        await telegram_file.download_as_bytearray(buffer)
        image_bytes = bytes(buffer)
    except Exception:
        logger.exception("تعذّر تنزيل صورة توثيق للمستخدم %s", user.id)
        await message.reply_text(
            "تعذّر استلام الملف الآن. لم تُحفظ الصورة؛ أعد إرسالها بعد قليل."
        )
        return _verification_state_for_kind(kind)

    try:
        await asyncio.to_thread(
            identity_image_checks.validate_identity_image,
            kind,
            image_bytes,
        )
        encrypted = await asyncio.to_thread(
            verified_reporters.encrypt_document,
            kind,
            image_bytes,
        )
    except identity_image_checks.IdentityImageCheckError as exc:
        if exc.args and exc.args[0] == "inspection_unavailable":
            logger.exception(
                "Local identity-image inspection is unavailable for user %s",
                user.id,
            )
        await message.reply_text(
            _verification_image_error_message(str(exc))
        )
        return _verification_state_for_kind(kind)
    except verified_reporters.VerificationUnavailable:
        logger.exception("Identity verification encryption became unavailable")
        await _discard_verification_capture(context, user.id)
        await _send_clean_prompt(
            update,
            context,
            "تعذّر التشفير الآمن، لذلك لم تُحفظ الصورة. ابدأ طلبًا جديدًا لاحقًا.",
            reply_markup=main_menu_kb(),
        )
        return ConversationHandler.END
    except Exception:
        logger.exception("Unexpected local identity-image check failure")
        await message.reply_text(
            "تعذّر فحص الصورة محليًا، لذلك لم تُقبل. حاول مرة أخرى لاحقًا."
        )
        return _verification_state_for_kind(kind)

    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(minutes=VERIFICATION_TICKET_MINUTES)
    ).isoformat(timespec="seconds")
    try:
        ticket_extended = await _db(context).extend_verification_ticket(
            user.id, ticket_hash, expires_at
        )
    except Exception:
        logger.exception("Could not extend verified-reporter ticket for user %s", user.id)
        context.user_data.pop(VERIFICATION_TICKET_KEY, None)
        context.user_data.pop(VERIFICATION_CAPTURES_KEY, None)
        context.user_data.pop(VERIFICATION_STEP_KEY, None)
        await message.reply_text(
            "تعذّر متابعة التوثيق بسبب مشكلة مؤقتة في الحفظ. لم تُرسل الصور للمراجعة."
        )
        return ConversationHandler.END
    if not ticket_extended:
        await _discard_verification_capture(context, user.id)
        await _send_clean_prompt(
            update,
            context,
            "انتهت جلسة التوثيق. ابدأ طلبًا جديدًا من القائمة الرئيسية.",
            reply_markup=main_menu_kb(),
        )
        return ConversationHandler.END

    captures[kind] = encrypted
    context.user_data[VERIFICATION_CAPTURES_KEY] = captures
    if next_state is not None:
        next_kind = (
            "id_front" if next_state == ASK_VERIFICATION_ID_FRONT else "id_back"
        )
        context.user_data[VERIFICATION_STEP_KEY] = next_kind
        prompts = {
            "id_front": (
                "اجتزت الصورة الفحص الأولي. أرسل الآن الجهة الأمامية للهوية "
                "كملف صورة كامل وواضح."
            ),
            "id_back": (
                "اجتزت الصورة الفحص الأولي. أرسل الآن الجهة الخلفية للهوية "
                "كملف صورة كامل وواضح."
            ),
        }
        await _send_clean_prompt(update, context, prompts[next_kind])
        return next_state

    if set(captures) != verified_reporters.DOCUMENT_KINDS:
        logger.error("Incomplete verified-reporter image sequence for user %s", user.id)
        await _discard_verification_capture(context, user.id)
        await _send_clean_prompt(
            update,
            context,
            "لم يكتمل ترتيب الصور، لذلك لم يُرسل الطلب. ابدأه من جديد.",
            reply_markup=main_menu_kb(),
        )
        return ConversationHandler.END
    try:
        request_id = await _db(context).submit_verification_documents(
            user.id,
            ticket_hash,
            captures,
        )
    except Exception:
        logger.exception(
            "Could not save encrypted verified-reporter request for user %s",
            user.id,
        )
        await _discard_verification_capture(context, user.id)
        await _send_clean_prompt(
            update,
            context,
            "تعذّر حفظ الطلب بأمان، ولم يُرسل للمراجعة. ابدأ طلبًا جديدًا لاحقًا.",
            reply_markup=main_menu_kb(),
        )
        return ConversationHandler.END
    if request_id is None:
        await _discard_verification_capture(context, user.id)
        await _send_clean_prompt(
            update,
            context,
            "انتهت جلسة التوثيق قبل اكتمال الإرسال. ابدأ طلبًا جديدًا.",
            reply_markup=main_menu_kb(),
        )
        return ConversationHandler.END

    active_prompt = context.user_data.get(ACTIVE_PROMPT_KEY)
    context.user_data.clear()
    if active_prompt:
        context.user_data[ACTIVE_PROMPT_KEY] = active_prompt
    await _send_clean_prompt(
        update,
        context,
        "✅ اكتمل إرسال طلبك للمراجعة البشرية. الفحص المحلي لا يثبت أصالة الهوية "
        "أو أن الصورة حيّة. سنبلغك عند صدور القرار.",
        reply_markup=main_menu_kb(),
    )
    return ConversationHandler.END


async def receive_verification_selfie(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    return await _receive_verification_image(
        update,
        context,
        "selfie",
        ASK_VERIFICATION_ID_FRONT,
    )


async def receive_verification_id_front(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    return await _receive_verification_image(
        update,
        context,
        "id_front",
        ASK_VERIFICATION_ID_BACK,
    )


async def receive_verification_id_back(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    return await _receive_verification_image(
        update,
        context,
        "id_back",
        None,
    )


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
        or report.get("status") != STATUS_AWAITING_INFO
        or (
            report.get("followup_due_at")
            and report["followup_due_at"]
            <= datetime.now(timezone.utc).isoformat(timespec="seconds")
        )
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
            await message.reply_text("اكتب اعتراضًا بين حرفين و2000 حرف.")
        return ASK_REPORT_APPEAL

    db = _db(context)
    appeal_id = await db.add_report_appeal(report_id, user.id, text)
    if appeal_id is None:
        context.user_data.pop("report_appeal_id", None)
        await message.reply_text("لم يعد هذا البلاغ قابلًا لتقديم اعتراض جديد.")
        return ConversationHandler.END
    report = await db.get_by_id(report_id)
    delivered = 0
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    f"✉️ طلب إعادة مراجعة للبلاغ {report.get('seq') or report_id}\n"
                    f"المبلّغ: {utils.esc(user.full_name or '-')} ({user.id})\n\n"
                    f"{utils.esc(text)}\n\n"
                    "لم يُعَد فتح البلاغ تلقائيًا؛ راجعه من لوحة الإدارة."
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
        "تم حفظ اعتراضك وإحالته للمراجعة؛ لم يُعَد فتح البلاغ تلقائيًا."
        if delivered
        else "تم حفظ اعتراضك، لكن تعذر إيصال إشعار للمشرفين حاليًا. "
        "سيبقى البلاغ في قائمة المراجعة.",
        reply_markup=main_menu_kb(),
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
        reply_markup=main_menu_kb(),
    )
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
    if data != f"{USR_PREFIX}verify-status":
        await _discard_verification_capture(context, query.from_user.id)

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
    if data != f"{USR_PREFIX}home":
        _remember_user_prompt(context, query.message)
    parts = data.split(":")

    # --- القائمة الرئيسية ---
    if parts[1] == "menu":
        context.user_data.clear()
        _pending_app_users.discard(query.from_user.id)
        welcome, parse_mode = await _welcome_content(db)
        if await guards.is_emergency(db):
            if parse_mode == ParseMode.HTML:
                welcome = texts.REPORTING_PAUSED + "\n\n" + welcome
            else:
                welcome = "🛑 استقبال البلاغات الجديدة متوقف مؤقتًا.\n\n" + welcome
        await query.edit_message_text(
            welcome,
            parse_mode=parse_mode,
            reply_markup=main_menu_kb(),
        )
        _remember_user_prompt(context, query.message)
        return ConversationHandler.END

    # The receipt is a record; opening the menu must not overwrite it.
    if parts[1] == "home":
        context.user_data.clear()
        _pending_app_users.discard(query.from_user.id)
        welcome, parse_mode = await _welcome_content(db)
        if await guards.is_emergency(db):
            if parse_mode == ParseMode.HTML:
                welcome = texts.REPORTING_PAUSED + "\n\n" + welcome
            else:
                welcome = "🛑 استقبال البلاغات الجديدة متوقف مؤقتًا.\n\n" + welcome
        prompt = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=welcome,
            parse_mode=parse_mode,
            reply_markup=main_menu_kb(),
        )
        _remember_user_prompt(context, prompt)
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
        return await _show_report_step(update, context, ASK_CATEGORY)

    # --- فتح التطبيق ---
    # أُزيل زر فتح التطبيق من الأزرار المضمنة (قرار الواجهة الاحترافية)؛
    # التطبيق يظل متاحًا عبر أمر /app فقط إن وُجد رابطه.
    if parts[1] == "app":
        await query.answer(texts.APP_NOT_READY, show_alert=False)
        return

    # --- بلاغاتي: بطاقات كل بلاغات المتبلّغ (بدل آخر بلاغ فقط) ---
    if parts[1] == "status":
        context.user_data.clear()
        _pending_app_users.discard(query.from_user.id)
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
        _remember_user_prompt(context, query.message)
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
                rid,
                bool(
                    r.get("followup_requested_at")
                    and not r.get("followup_response")
                    and r.get("status") == STATUS_AWAITING_INFO
                ),
                r.get("status") == STATUS_REJECTED,
            ),
        )
        _remember_user_prompt(context, query.message)
        return ConversationHandler.END

    if parts[1] == "followup" and len(parts) == 3 and parts[2].isdigit():
        rid = int(parts[2])
        report = await db.get_by_id(rid)
        if (
            not report
            or int(report.get("user_id") or 0) != query.from_user.id
            or not report.get("followup_requested_at")
            or report.get("followup_response")
            or report.get("status") != STATUS_AWAITING_INFO
            or (
                report.get("followup_due_at")
                and report["followup_due_at"]
                <= datetime.now(timezone.utc).isoformat(timespec="seconds")
            )
        ):
            await query.answer("لا يوجد طلب معلومات مفتوح لهذا البلاغ.", show_alert=True)
            return ConversationHandler.END
        context.user_data["followup_report_id"] = rid
        context.user_data["report_state"] = ASK_FOLLOWUP
        await query.edit_message_text(
            texts.ASK_FOLLOWUP,
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_inline_kb(),
        )
        _remember_user_prompt(context, query.message)
        return ASK_FOLLOWUP

    if parts[1] == "appeal" and len(parts) == 3 and parts[2].isdigit():
        rid = int(parts[2])
        report = await db.get_by_id(rid)
        if (
            query.message.chat.type != "private"
            or not report
            or int(report.get("user_id") or 0) != query.from_user.id
            or report.get("status") != STATUS_REJECTED
        ):
            await query.answer("لا يمكن إرسال اعتراض لهذا البلاغ.", show_alert=True)
            return ConversationHandler.END
        context.user_data.clear()
        context.user_data["report_appeal_id"] = rid
        await query.edit_message_text(
            "أرسل اعتراضك أو المعلومات الإضافية المتعلقة بهذا البلاغ "
            "(حتى 2000 حرف). سيصل الطلب إلى المشرف للمراجعة، ولن يُعاد فتح "
            "البلاغ تلقائيًا.",
            reply_markup=cancel_inline_kb(),
        )
        _remember_user_prompt(context, query.message)
        return ASK_REPORT_APPEAL

    # --- عن النظام ---
    if parts[1] == "privacy":
        privacy_url = await _privacy_policy_url(db)
        if privacy_url:
            message_text = (
                "يمكنك قراءة سياسة الخصوصية عبر الرابط التالي. "
                "راجعها قبل إرسال بلاغ أو بدء توثيق المراسل."
            )
        else:
            message_text = (
                "لم يُضبط رابط سياسة الخصوصية بعد. لا تُرسل بيانات حساسة "
                "ضمن البلاغ قبل مراجعة الإدارة."
            )
        await query.edit_message_text(
            message_text,
            reply_markup=keyboards.privacy_policy_kb(privacy_url),
        )
        _remember_user_prompt(context, query.message)
        return ConversationHandler.END

    if parts[1] == "about":
        context.user_data.clear()
        _pending_app_users.discard(query.from_user.id)
        await query.edit_message_text(
            texts.ABOUT_SYSTEM, parse_mode=ParseMode.HTML,
            reply_markup=back_to_menu_kb(),
        )
        _remember_user_prompt(context, query.message)
        return ConversationHandler.END

    # --- تواصل مع الإدارة ---
    if parts[1] == "contact":
        await query.edit_message_text(
            texts.ASK_SUPPORT,
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_inline_kb(),
        )
        context.user_data["support_pending"] = True
        _remember_user_prompt(context, query.message)
        return ASK_SUPPORT

    if parts[1] == "verify-status":
        status = await db.verification_status(query.from_user.id)
        if not status:
            message_text = "لم تقدّم طلب توثيق. يمكنك التقديم اختياريًا من القائمة الرئيسية."
        elif status["status"] == "verified":
            message_text = "✅ حسابك موثّق كمراسل. هذه الشارة لا تعني اعتماد أي بلاغ تلقائيًا."
        elif status["status"] == "capturing":
            message_text = (
                "طلب التوثيق غير مكتمل بعد. الصور لا تُحفظ للمراجعة حتى تنتهي "
                "من إرسال الصور الثلاث واجتيازها الفحص المحلي."
            )
        elif status["status"] == "pending" and status.get("has_documents"):
            message_text = "⏳ طلب التوثيق قيد المراجعة الإدارية."
        elif status["status"] == "pending":
            message_text = (
                "تعذّر العثور على صور الطلب بعد استعادة بيانات النظام. "
                "يمكنك بدء طلب جديد؛ لم تُستعد الصور من النسخ الاحتياطية."
            )
        elif status["status"] == "rejected":
            message_text = "لم يُعتمد طلب التوثيق. يمكنك إعادة التقديم من القائمة الرئيسية."
        else:
            message_text = "لم يكتمل إرسال طلب التوثيق. ابدأ من جديد عند رغبتك."
        await query.edit_message_text(
            message_text,
            reply_markup=back_to_menu_kb(),
        )
        _remember_user_prompt(context, query.message)
        if context.user_data.get(VERIFICATION_STEP_KEY) in {
            "selfie", "id_front", "id_back",
        }:
            return _verification_state_for_kind(
                context.user_data[VERIFICATION_STEP_KEY]
            )
        return context.user_data.get("report_state")

    if parts[1] == "verify":
        if query.message.chat.type != "private":
            await query.answer("ابدأ التوثيق في المحادثة الخاصة مع البوت.", show_alert=True)
            return context.user_data.get("report_state")
        if any(
            key in context.user_data
            for key in ("report_category", "app_draft", "followup_report_id")
        ):
            await query.answer(
                "أكمل البلاغ الحالي أو ألغِه قبل بدء التوثيق.",
                show_alert=True,
            )
            return context.user_data.get("report_state")
        privacy_url = await _privacy_policy_url(db)
        disclosure, consent_version = await _verification_consent(db)
        await query.edit_message_text(
            disclosure,
            reply_markup=keyboards.verification_consent_kb(
                privacy_url, consent_version
            ),
        )
        _remember_user_prompt(context, query.message)
        return ConversationHandler.END

    if parts[1] == "verify-consent":
        if query.message.chat.type != "private":
            await query.answer("ابدأ التوثيق في المحادثة الخاصة مع البوت.", show_alert=True)
            return context.user_data.get("report_state")
        if any(
            key in context.user_data
            for key in ("report_category", "app_draft", "followup_report_id")
        ):
            await query.answer(
                "أكمل البلاغ الحالي أو ألغِه قبل بدء التوثيق.",
                show_alert=True,
            )
            return context.user_data.get("report_state")
        if not verified_reporters.is_configured():
            await query.answer(
                "التوثيق غير مفعّل بعد؛ إعداد التشفير الآمن غير مكتمل.",
                show_alert=True,
            )
            return ConversationHandler.END
        disclosure, consent_version = await _verification_consent(db)
        if len(parts) != 3 or parts[2] != consent_version:
            await query.edit_message_text(
                disclosure,
                reply_markup=keyboards.verification_consent_kb(
                    await _privacy_policy_url(db), consent_version
                ),
            )
            _remember_user_prompt(context, query.message)
            return ConversationHandler.END
        consented_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        status = await db.verification_status(query.from_user.id)
        if status and status.get("status") == "verified":
            updated = await db.update_verification_consent(
                query.from_user.id, consented_at, consent_version
            )
            if not updated:
                await query.answer(
                    "تعذر تحديث الموافقة؛ أعد المحاولة لاحقًا.",
                    show_alert=True,
                )
                return ConversationHandler.END
            await db.audit(
                query.from_user.id,
                "reporter_consent_updated",
                str(query.from_user.id),
                f"consent_version={consent_version}",
            )
            await query.edit_message_text(
                "تم تسجيل موافقتك على إفصاح التوثيق المحدّث. "
                "لم نجمع وثائق إضافية.",
                reply_markup=main_menu_kb(),
            )
            _remember_user_prompt(context, query.message)
            return ConversationHandler.END
        await db.cancel_verification_capture(query.from_user.id)
        active_prompt = context.user_data.get(ACTIVE_PROMPT_KEY)
        context.user_data.clear()
        if active_prompt:
            context.user_data[ACTIVE_PROMPT_KEY] = active_prompt
        context.user_data["verification_flow"] = True
        context.user_data["verification_consent_at"] = consented_at
        context.user_data["verification_consent_version"] = consent_version
        await _send_clean_prompt(
            update,
            context,
            "شارِك رقم هاتفك السوري عبر زر تيليجرام للانتقال إلى خطوات التوثيق. "
            "يمكنك الإلغاء في أي وقت.",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboards.verification_contact_kb(),
        )
        return ASK_VERIFICATION_PHONE

    if parts[1] == "back" and len(parts) == 3:
        target_states = {
            "category": ASK_CATEGORY,
            "platform": ASK_PLATFORM,
            "description": ASK_DESC,
            "photo": ASK_PHOTO,
            "link": ASK_LINK,
            "location": ASK_LOCATION,
            "contact": ASK_CONTACT,
        }
        current = context.user_data.get("report_state")
        allowed_back = {
            ASK_PLATFORM: {"category"},
            ASK_DESC: {
                "platform"
                if context.user_data.get("report_category") == "social"
                else "category"
            },
            ASK_PHOTO: {"description"},
            ASK_LINK: {"photo"},
            ASK_LOCATION: {"photo"},
            ASK_CONTACT: {
                {
                    "field": "location",
                    "social": "link",
                }.get(context.user_data.get("report_category"), "photo")
            },
            CONFIRM: {"contact"},
        }
        target = parts[2]
        if target not in target_states or target not in allowed_back.get(current, set()):
            await query.edit_message_text(
                "انتهت هذه الخطوة. ابدأ من القائمة الرئيسية.",
                reply_markup=back_to_menu_kb(),
            )
            context.user_data.clear()
            _remember_user_prompt(context, query.message)
            return ConversationHandler.END
        return await _show_report_step(update, context, target_states[target])

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
        if category == "social":
            return await _show_report_step(update, context, ASK_PLATFORM)
        return await _show_report_step(update, context, ASK_DESC)

    if parts[1] == "platform" and len(parts) == 3:
        platform_key = parts[2]
        if platform_key not in SOCIAL_PLATFORM_LABELS:
            await query.answer("المنصة غير معروفة.", show_alert=True)
            return ASK_PLATFORM
        context.user_data["platform"] = SOCIAL_PLATFORM_LABELS[platform_key]
        context.user_data["platform_key"] = platform_key
        return await _show_report_step(update, context, ASK_DESC)

    if parts[1] == "skip" and len(parts) == 3:
        step = parts[2]
        if step == "photo":
            context.user_data.pop("optional_prompt", None)
            return await _advance_after_evidence(update, context)
        if step == "link" and context.user_data.get("report_category") == "social":
            context.user_data.pop("optional_prompt", None)
            try:
                await _assess_link_and_description(context, db, "")
            except ValueError as exc:
                await _send_clean_prompt(
                    update,
                    context,
                    str(exc),
                    parse_mode=ParseMode.HTML,
                    reply_markup=optional_step_kb("link"),
                )
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
        return await _show_report_step(update, context, ASK_DESC)

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
        _remember_user_prompt(context, query.message)
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
    await _discard_verification_capture(
        context,
        update.effective_user.id if update.effective_user else None,
    )
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
            ASK_REPORT_APPEAL: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_report_appeal,
                ),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, unmatched),
            ],
            ASK_VERIFICATION_PHONE: [
                MessageHandler(filters.Regex(CANCEL_RE), cmd_cancel),
                MessageHandler(
                    (filters.CONTACT | filters.TEXT) & ~filters.COMMAND,
                    receive_verification_phone,
                ),
                MessageHandler(filters.ALL & ~filters.COMMAND, unmatched),
            ],
            ASK_VERIFICATION_SELFIE: [
                MessageHandler(filters.Regex(CANCEL_RE), cmd_cancel),
                MessageHandler(
                    filters.Document.ALL | filters.PHOTO,
                    receive_verification_selfie,
                ),
                MessageHandler(filters.ALL & ~filters.COMMAND, unmatched),
            ],
            ASK_VERIFICATION_ID_FRONT: [
                MessageHandler(filters.Regex(CANCEL_RE), cmd_cancel),
                MessageHandler(
                    filters.Document.ALL | filters.PHOTO,
                    receive_verification_id_front,
                ),
                MessageHandler(filters.ALL & ~filters.COMMAND, unmatched),
            ],
            ASK_VERIFICATION_ID_BACK: [
                MessageHandler(filters.Regex(CANCEL_RE), cmd_cancel),
                MessageHandler(
                    filters.Document.ALL | filters.PHOTO,
                    receive_verification_id_back,
                ),
                MessageHandler(filters.ALL & ~filters.COMMAND, unmatched),
            ],
            ASK_SUPPORT: [
                MessageHandler(filters.Regex(CANCEL_RE), cmd_cancel),
                MessageHandler(filters.ALL & ~filters.COMMAND, receive_support_message),
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
