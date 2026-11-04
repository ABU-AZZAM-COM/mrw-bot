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
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telegram import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatAction, ParseMode
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
from bot import admin, control, keyboards, texts
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
    report_type_kb,
    social_platform_kb,
    optional_step_kb,
    contact_request_kb,
    location_request_kb,
)

logger = logging.getLogger(__name__)

from bot.callbacks import (  # noqa: E402
    on_user_callback,
)

from bot.app_flow import (  # noqa: E402
    cmd_app,
    keyboards_app_kb,
    app_data_received,
    app_photo_received,
)

from bot.report_flow import (  # noqa: E402
    _show_report_step,
    _start_report_flow,
    cmd_start,
    _save_photo,
    _advance_after_evidence,
    _ask_contact,
    _remember_optional_prompt,
    _clear_optional_prompt,
    _show_report_confirmation,
    _assess_link_and_description,
    _screen_uploaded_photo,
    _freeze_for_sensitive_image,
    receive_photo,
    remind_photo,
    receive_link,
    remind_link,
    receive_desc,
    remind_desc,
    receive_location,
    receive_contact,
    confirm_save,
)

from bot.verification_flow import (  # noqa: E402
    receive_verification_phone,
    _discard_verification_capture,
    _verification_image_error_message,
    _verification_state_for_kind,
    _receive_verification_image,
    _receive_verification_supplement_image,
    _notify_verification_supplement_reviewers,
    receive_verification_selfie,
    receive_verification_id_front,
    receive_verification_id_back,
)

from bot.helpers import (  # noqa: E402
    _typing_while_processing,
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
    ASK_VERIFICATION_SUPPLEMENT,
    ASK_TYPE,
    SUPPORT_MAX_LENGTH,
    SUPPORT_MAX_WORDS,
    SUPPORT_MAX_PER_10_MINUTES,
    SUPPORT_MAX_PER_DAY,
    VERIFICATION_TICKET_KEY,
    VERIFICATION_CAPTURES_KEY,
    VERIFICATION_STEP_KEY,
    VERIFICATION_TICKET_MINUTES,
    CANCEL_RE,
    CONFIRM_RE,
    _SemanticTextFilter,
    USR_PREFIX,
    MY_REPORTS_MAX,
    ACTIVE_PROMPT_KEY,
    _pending_app_users,
    AppPhotoPending,
    _db,
    _welcome_content,
    _main_menu_content,
    VERIFIED_REPORTER_BADGE,
    _is_verified_reporter,
    _with_verified_reporter_badge,
    _privacy_policy_url,
    _verification_consent,
    _remember_user_prompt,
    _delete_user_message,
    _clear_user_prompt,
    _clear_welcome_photo,
    _send_clean_prompt,
    _contact_back_step,
)


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
        vtype=utils.esc(
            report.get("violation_type") or texts.INLINE_MESSAGES["unspecified_value"]
        ),
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


async def confirm_save_from_draft(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database) -> None:
    """حفظ بلاغ من مسودة التطبيق بعد الضغط على زر تأكيد الإرسال."""
    query = update.callback_query
    user = query.from_user
    ud = context.user_data
    draft = ud.pop("app_draft", None)
    _pending_app_users.discard(user.id)

    if not draft:
        await query.edit_message_text(
            texts.INLINE_MESSAGES["invalid_draft"],
            reply_markup=back_to_menu_kb(),
        )
        _remember_user_prompt(context, query.message)
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
    if not message or not chat or not guards.is_private_chat(chat.type):
        return
    await message.reply_text(
        texts.WELCOME_HINT,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(user.id if user else None),
    )


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
    except Exception:  # noqa: BLE001 — سجّل فشل الإشعار دون استثناء ثانٍ
        logger.warning(
            "تعذر إرسال رسالة الخطأ للمستخدم %s",
            update.effective_user.id if update.effective_user else "?",
            exc_info=True,
        )


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
                MessageHandler(_SemanticTextFilter("cancel"), cmd_cancel),
                MessageHandler(filters.ALL & ~filters.COMMAND, unmatched),
            ],
            ASK_TYPE: [
                MessageHandler(_SemanticTextFilter("cancel"), cmd_cancel),
                MessageHandler(filters.ALL & ~filters.COMMAND, unmatched),
            ],
            ASK_PLATFORM: [
                MessageHandler(_SemanticTextFilter("cancel"), cmd_cancel),
                MessageHandler(filters.ALL & ~filters.COMMAND, unmatched),
            ],
            ASK_DESC: [
                MessageHandler(_SemanticTextFilter("cancel"), cmd_cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_desc),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, remind_desc),
            ],
            ASK_PHOTO: [
                MessageHandler(_SemanticTextFilter("cancel"), cmd_cancel),
                MessageHandler(filters.PHOTO | filters.Document.IMAGE, receive_photo),
                MessageHandler(
                    ~filters.PHOTO & ~filters.Document.IMAGE & ~filters.COMMAND,
                    remind_photo,
                ),
            ],
            ASK_LINK: [
                MessageHandler(_SemanticTextFilter("cancel"), cmd_cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_link),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, remind_link),
            ],
            ASK_LOCATION: [
                MessageHandler(_SemanticTextFilter("cancel"), cmd_cancel),
                MessageHandler(
                    (filters.LOCATION | filters.TEXT) & ~filters.COMMAND,
                    receive_location,
                ),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, unmatched),
            ],
            ASK_CONTACT: [
                MessageHandler(_SemanticTextFilter("cancel"), cmd_cancel),
                MessageHandler(
                    (filters.CONTACT | filters.TEXT) & ~filters.COMMAND,
                    receive_contact,
                ),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, unmatched),
            ],
            CONFIRM: [
                MessageHandler(_SemanticTextFilter("confirm"), confirm_save),
                MessageHandler(_SemanticTextFilter("cancel"), cmd_cancel),
                MessageHandler(~_SemanticTextFilter("confirm"), unmatched),
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
                MessageHandler(
                    _SemanticTextFilter("back"), verification_phone_back
                ),
                MessageHandler(
                    _SemanticTextFilter("cancel"), verification_phone_back
                ),
                MessageHandler(
                    (filters.CONTACT | filters.TEXT) & ~filters.COMMAND,
                    receive_verification_phone,
                ),
                MessageHandler(filters.ALL & ~filters.COMMAND, unmatched),
            ],
            ASK_VERIFICATION_SELFIE: [
                MessageHandler(_SemanticTextFilter("cancel"), cmd_cancel),
                MessageHandler(
                    filters.Document.ALL | filters.PHOTO,
                    receive_verification_selfie,
                ),
                MessageHandler(filters.ALL & ~filters.COMMAND, unmatched),
            ],
            ASK_VERIFICATION_ID_FRONT: [
                MessageHandler(_SemanticTextFilter("cancel"), cmd_cancel),
                MessageHandler(
                    filters.Document.ALL | filters.PHOTO,
                    receive_verification_id_front,
                ),
                MessageHandler(filters.ALL & ~filters.COMMAND, unmatched),
            ],
            ASK_VERIFICATION_ID_BACK: [
                MessageHandler(_SemanticTextFilter("cancel"), cmd_cancel),
                MessageHandler(
                    filters.Document.ALL | filters.PHOTO,
                    receive_verification_id_back,
                ),
                MessageHandler(filters.ALL & ~filters.COMMAND, unmatched),
            ],
            ASK_VERIFICATION_SUPPLEMENT: [
                MessageHandler(_SemanticTextFilter("cancel"), cmd_cancel),
                MessageHandler(filters.PHOTO, _receive_verification_supplement_image),
                MessageHandler(filters.ALL & ~filters.COMMAND, unmatched),
            ],
            ASK_SUPPORT: [
                MessageHandler(_SemanticTextFilter("cancel"), cmd_cancel),
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
