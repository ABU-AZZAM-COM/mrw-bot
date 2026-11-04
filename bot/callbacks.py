"""معالجات أزرار المستخدم (Inline keyboards).

نُقلت من ``bot/handlers.py``. تستدعي تدفقات البلاغ والتوثيق عبر أغلفة تربط
الأسماء وقت الطلب من ``bot.handlers``، فيبقى الاستبدال في الاختبارات فعالاً.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import ContextTypes, ConversationHandler

from bot import control, keyboards, texts
from bot.helpers import (
    ACTIVE_PROMPT_KEY,
    ASK_CATEGORY,
    ASK_CONTACT,
    ASK_DESC,
    ASK_FOLLOWUP,
    ASK_LINK,
    ASK_LOCATION,
    ASK_PHOTO,
    ASK_PLATFORM,
    ASK_REPORT_APPEAL,
    ASK_TYPE,
    ASK_VERIFICATION_PHONE,
    ASK_VERIFICATION_SUPPLEMENT,
    CONFIRM,
    MY_REPORTS_MAX,
    USR_PREFIX,
    VERIFICATION_STEP_KEY,
    _pending_app_users,
)
from bot.keyboards import (
    SOCIAL_PLATFORM_LABELS,
    back_to_menu_kb,
    cancel_inline_kb,
    main_menu_kb,
    my_reports_kb,
    optional_step_kb,
    report_category_kb,
    report_open_kb,
)
from database import STATUS_AWAITING_INFO, STATUS_REJECTED
from services import guards, utils, verified_reporters
from services.report_taxonomy import REPORT_CATEGORIES

logger = logging.getLogger(__name__)


def _late_bound(name: str):
    """غلاف يحلّ الاسم من ``bot.handlers`` وقت الطلب لا وقت التركيب."""

    def call(*args, **kwargs):
        from bot import handlers

        return getattr(handlers, name)(*args, **kwargs)

    return call


_advance_after_evidence = _late_bound("_advance_after_evidence")
_ask_contact = _late_bound("_ask_contact")
_assess_link_and_description = _late_bound("_assess_link_and_description")
_clear_user_prompt = _late_bound("_clear_user_prompt")
_clear_welcome_photo = _late_bound("_clear_welcome_photo")
_db = _late_bound("_db")
_discard_verification_capture = _late_bound("_discard_verification_capture")
_is_verified_reporter = _late_bound("_is_verified_reporter")
_main_menu_content = _late_bound("_main_menu_content")
_my_reports_text = _late_bound("_my_reports_text")
_privacy_policy_url = _late_bound("_privacy_policy_url")
_remember_user_prompt = _late_bound("_remember_user_prompt")
_send_clean_prompt = _late_bound("_send_clean_prompt")
_show_report_confirmation = _late_bound("_show_report_confirmation")
_show_report_step = _late_bound("_show_report_step")
_start_report_flow = _late_bound("_start_report_flow")
_verification_consent = _late_bound("_verification_consent")
_verification_state_for_kind = _late_bound("_verification_state_for_kind")
_with_verified_reporter_badge = _late_bound("_with_verified_reporter_badge")
confirm_save = _late_bound("confirm_save")
confirm_save_from_draft = _late_bound("confirm_save_from_draft")




def _late_bound(name: str):
    """غلاف يحلّ الاسم من ``bot.handlers`` وقت الطلب لا وقت التركيب."""

    def call(*args, **kwargs):
        from bot import handlers

        return getattr(handlers, name)(*args, **kwargs)

    return call


async def on_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    """يعالج كل ضغطة على زر مخصص للمستخدم في القائمة الرئيسية أو أثناء الإبلاغ."""
    query = update.callback_query
    db = _db(context)
    data = query.data or ""
    parts = data.split(":")
    if len(parts) < 2 or not parts[1]:
        await query.answer(texts.UNKNOWN_BUTTON, show_alert=True)
        return ConversationHandler.END
    if data not in {
        f"{USR_PREFIX}verify-status",
        f"{USR_PREFIX}verify-supplement",
    }:
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

    if data != f"{USR_PREFIX}home":
        _remember_user_prompt(context, query.message)
    if len(parts) > 1:
        try:
            await db.record_button_press(parts[1][:64])
        except Exception:  # noqa: BLE001
            logger.debug("تعذر تسجيل ضغطة الزر", exc_info=True)

    # --- القائمة الرئيسية ---
    if parts[1] == "menu":
        context.user_data.clear()
        _pending_app_users.discard(query.from_user.id)
        welcome, parse_mode = await _main_menu_content(db, query.from_user.id)
        await query.edit_message_text(
            welcome,
            parse_mode=parse_mode,
            reply_markup=main_menu_kb(update.effective_user.id if update.effective_user else None),
        )
        _remember_user_prompt(context, query.message)
        return ConversationHandler.END

    # The receipt is a record; opening the menu must not overwrite it.
    if parts[1] == "home":
        context.user_data.clear()
        _pending_app_users.discard(query.from_user.id)
        welcome, parse_mode = await _main_menu_content(db, query.from_user.id)
        prompt = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=welcome,
            parse_mode=parse_mode,
            reply_markup=main_menu_kb(update.effective_user.id if update.effective_user else None),
        )
        _remember_user_prompt(context, prompt)
        return ConversationHandler.END

    if parts[1] == "continue":
        await _clear_user_prompt(context)
        await _clear_welcome_photo(context)
        prompt = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=await _with_verified_reporter_badge(
                texts.MAIN_MENU_PROMPT, db, query.from_user.id
            ),
            reply_markup=main_menu_kb(query.from_user.id),
        )
        _remember_user_prompt(context, prompt)
        return ConversationHandler.END

    # --- بلاغ جديد ---
    if parts[1] == "report":
        return await _start_report_flow(update, context, db)

    # --- فتح التطبيق ---
    # أُزيل زر فتح التطبيق من الأزرار المضمنة (قرار الواجهة الاحترافية)؛
    # التطبيق يظل متاحًا عبر أمر /app فقط إن وُجد رابطه.
    if parts[1] == "app":
        await query.edit_message_text(
            texts.APP_NOT_READY,
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_menu_kb(),
        )
        _remember_user_prompt(context, query.message)
        return

    # --- بلاغاتي: بطاقات كل بلاغات المتبلّغ (بدل آخر بلاغ فقط) ---
    if parts[1] == "status":
        context.user_data.clear()
        _pending_app_users.discard(query.from_user.id)
        try:
            page = max(0, int(parts[2])) if len(parts) > 2 else 0
        except ValueError:
            await query.answer(texts.UNKNOWN_BUTTON, show_alert=True)
            return ConversationHandler.END
        reports, total = await db.list_user_reports(
            query.from_user.id,
            limit=MY_REPORTS_MAX,
            offset=page * MY_REPORTS_MAX,
        )
        last_page = max(0, (total - 1) // MY_REPORTS_MAX)
        if page > last_page:
            page = last_page
            reports, total = await db.list_user_reports(
                query.from_user.id,
                limit=MY_REPORTS_MAX,
                offset=page * MY_REPORTS_MAX,
            )
        if not reports:
            await query.edit_message_text(
                await _with_verified_reporter_badge(
                    texts.NO_REPORTS_YET, db, query.from_user.id
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=back_to_menu_kb(),
            )
            return ConversationHandler.END
        is_verified = await _is_verified_reporter(db, query.from_user.id)
        await query.edit_message_text(
            _my_reports_text(reports, total, page, verified=is_verified),
            parse_mode=ParseMode.HTML,
            reply_markup=my_reports_kb(
                reports,
                page=page,
                has_previous=page > 0,
                has_next=(page + 1) * MY_REPORTS_MAX < total,
            ),
        )
        _remember_user_prompt(context, query.message)
        return ConversationHandler.END

    # --- بطاقة تفصيل بلاغ من «بلاغاتي» (usr:r:<id>) ---
    if parts[1] == "r" and len(parts) > 2 and parts[2].isdigit():
        rid = int(parts[2])
        r = await db.get_by_id(rid)
        # حماية: لا يفتح المتبلّغ بطاقة بلاغٍ ليس له
        if not r or int(r.get("user_id") or 0) != query.from_user.id:
            await query.edit_message_text(
                texts.REPORT_NOT_FOUND, reply_markup=back_to_menu_kb()
            )
            _remember_user_prompt(context, query.message)
            return ConversationHandler.END
        location_parts = []
        if r.get("place"):
            location_parts.append(str(r["place"]))
        if r.get("latitude") is not None and r.get("longitude") is not None:
            location_parts.append(f"{r['latitude']}, {r['longitude']}")
        location = utils.esc(" · ".join(location_parts) or "-")
        report_text = texts.MY_REPORT_OPEN.format(
            seq=r["seq"],
            time=utils.fmt_local(r["created_at"]),
            status=texts.badge(r["status"]),
            status_detail=texts.status_explanation(r["status"]),
            category=utils.esc(r.get("category") or "-"),
            type=utils.esc(r.get("violation_type") or "-"),
            platform=utils.esc(r.get("platform") or "-"),
            location=location,
            desc=utils.esc(r.get("description") or "-"),
            link=(
                f'<a href="{utils.esc(str(r["post_link"]))}">فتح الرابط الأصلي</a>'
                if str(r.get("post_link") or "").startswith(("http://", "https://"))
                else "-"
            ),
        )
        await query.edit_message_text(
            await _with_verified_reporter_badge(
                report_text, db, query.from_user.id
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
            await query.edit_message_text(
                texts.INLINE_MESSAGES["followup_unavailable"],
                reply_markup=back_to_menu_kb(),
            )
            _remember_user_prompt(context, query.message)
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
            await query.edit_message_text(
                texts.INLINE_MESSAGES["appeal_unavailable"],
                reply_markup=back_to_menu_kb(),
            )
            _remember_user_prompt(context, query.message)
            return ConversationHandler.END
        context.user_data.clear()
        context.user_data["report_appeal_id"] = rid
        await query.edit_message_text(
            texts.INLINE_MESSAGES["appeal_prompt"],
            reply_markup=cancel_inline_kb(),
        )
        _remember_user_prompt(context, query.message)
        return ASK_REPORT_APPEAL

    # --- عن النظام ---
    if parts[1] == "privacy":
        privacy_url = await _privacy_policy_url(db)
        if privacy_url:
            message_text = texts.INLINE_MESSAGES["privacy_link_intro"]
        else:
            message_text = texts.PRIVACY_POLICY
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
        markup = keyboards.contact_app_kb()
        if markup is None:
            await query.edit_message_text(
                texts.APP_NOT_READY,
                parse_mode=ParseMode.HTML,
                reply_markup=back_to_menu_kb(),
            )
            _remember_user_prompt(context, query.message)
            return ConversationHandler.END
        await query.edit_message_text(
            "تواصل مع الإدارة عبر محادثة خاصة محفوظة داخل التطبيق.",
            reply_markup=markup,
        )
        _remember_user_prompt(context, query.message)
        return ConversationHandler.END

    if parts[1] == "settings":
        markup = keyboards.settings_app_kb()
        if markup is None:
            await query.edit_message_text(
                texts.APP_NOT_READY,
                parse_mode=ParseMode.HTML,
                reply_markup=back_to_menu_kb(),
            )
        else:
            await query.edit_message_text(
                "إعدادات التنبيهات وحالة التوثيق.",
                reply_markup=markup,
            )
        _remember_user_prompt(context, query.message)
        return ConversationHandler.END

    if parts[1] == "verify-supplement":
        status = await db.verification_status(query.from_user.id)
        request_id = (
            int(status["id"])
            if status
            and status.get("status") == "pending"
            and status.get("review_state") == "needs_info"
            else None
        )
        supplement = (
            await db.active_verification_supplement(request_id, query.from_user.id)
            if request_id is not None
            else None
        )
        if not supplement:
            await query.edit_message_text(
                texts.INLINE_MESSAGES["supplement_missing_status"],
                reply_markup=back_to_menu_kb(),
            )
            _remember_user_prompt(context, query.message)
            return ConversationHandler.END
        context.user_data["verification_supplement_request_id"] = request_id
        kind = next(
            (
                item for item in supplement["requested_documents"]
                if item not in supplement["uploaded_documents"]
            ),
            None,
        )
        labels = texts.VERIFICATION_DOCUMENT_LABELS
        await query.edit_message_text(
            texts.format_inline_message(
                "supplement_prompt",
                label=labels[kind]
            ),
            reply_markup=cancel_inline_kb(),
        )
        _remember_user_prompt(context, query.message)
        return ASK_VERIFICATION_SUPPLEMENT

    if parts[1] == "verify-status":
        status = await db.verification_status(query.from_user.id)
        if not status:
            message_text = texts.INLINE_MESSAGES["verification_status_not_started"]
        elif status["status"] == "verified":
            message_text = texts.INLINE_MESSAGES["verification_status_verified"]
        elif status["status"] == "capturing":
            message_text = texts.INLINE_MESSAGES["verification_status_capturing"]
        elif (
            status["status"] == "pending"
            and status.get("review_state") == "needs_info"
        ):
            required = status.get("supplement_requested_documents") or "[]"
            try:
                kinds = json.loads(required)
            except (TypeError, json.JSONDecodeError):
                kinds = []
            labels = texts.VERIFICATION_DOCUMENT_LABELS
            message_text = texts.format_inline_message(
                "verification_status_needs_info",
                documents="\n".join(
                    f"• {labels[kind]}" for kind in kinds if kind in labels
                ),
                message=status.get("supplement_message") or "",
            )
        elif status["status"] == "pending" and status.get("has_documents"):
            message_text = texts.INLINE_MESSAGES["verification_status_pending"]
        elif status["status"] == "pending":
            message_text = texts.INLINE_MESSAGES["verification_status_missing_documents"]
        elif status["status"] == "rejected":
            message_text = texts.INLINE_MESSAGES["verification_status_rejected"]
        else:
            message_text = texts.INLINE_MESSAGES["verification_status_incomplete"]
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
            await query.edit_message_text(
                texts.INLINE_MESSAGES["verification_private_only"],
                reply_markup=back_to_menu_kb(),
            )
            _remember_user_prompt(context, query.message)
            return context.user_data.get("report_state")
        if any(
            key in context.user_data
            for key in ("report_category", "app_draft", "followup_report_id")
        ):
            await query.edit_message_text(
                texts.INLINE_MESSAGES["verification_report_in_progress"],
                reply_markup=back_to_menu_kb(),
            )
            _remember_user_prompt(context, query.message)
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
            await query.edit_message_text(
                texts.INLINE_MESSAGES["verification_private_only"],
                reply_markup=back_to_menu_kb(),
            )
            _remember_user_prompt(context, query.message)
            return context.user_data.get("report_state")
        if any(
            key in context.user_data
            for key in ("report_category", "app_draft", "followup_report_id")
        ):
            await query.edit_message_text(
                texts.INLINE_MESSAGES["verification_report_in_progress"],
                reply_markup=back_to_menu_kb(),
            )
            _remember_user_prompt(context, query.message)
            return context.user_data.get("report_state")
        if not verified_reporters.is_configured():
            await query.edit_message_text(
                texts.INLINE_MESSAGES["verification_not_configured"],
                reply_markup=back_to_menu_kb(),
            )
            _remember_user_prompt(context, query.message)
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
                await query.edit_message_text(
                    texts.INLINE_MESSAGES["verification_consent_update_failed"],
                    reply_markup=back_to_menu_kb(),
                )
                _remember_user_prompt(context, query.message)
                return ConversationHandler.END
            await db.audit(
                query.from_user.id,
                "reporter_consent_updated",
                str(query.from_user.id),
                f"consent_version={consent_version}",
            )
            await query.edit_message_text(
                texts.INLINE_MESSAGES["verification_consent_updated"],
                reply_markup=main_menu_kb(update.effective_user.id if update.effective_user else None),
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
            texts.INLINE_MESSAGES["verification_phone_prompt"],
            parse_mode=ParseMode.HTML,
            reply_markup=keyboards.verification_contact_kb(),
        )
        return ASK_VERIFICATION_PHONE

    if parts[1] == "back" and len(parts) == 3:
        target_states = {
            "category": ASK_CATEGORY,
            "type": ASK_TYPE,
            "platform": ASK_PLATFORM,
            "description": ASK_DESC,
            "photo": ASK_PHOTO,
            "link": ASK_LINK,
            "location": ASK_LOCATION,
            "contact": ASK_CONTACT,
        }
        current = context.user_data.get("report_state")
        allowed_back = {
            ASK_TYPE: {"category"},
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
                texts.INLINE_MESSAGES["step_expired"],
                reply_markup=back_to_menu_kb(),
            )
            context.user_data.clear()
            _remember_user_prompt(context, query.message)
            return ConversationHandler.END
        return await _show_report_step(update, context, target_states[target])

    if parts[1] == "category" and len(parts) == 3:
        category = parts[2]
        if category not in REPORT_CATEGORIES:
            await query.edit_message_text(
                texts.INLINE_MESSAGES["unknown_category"],
                reply_markup=back_to_menu_kb(),
            )
            _remember_user_prompt(context, query.message)
            return ConversationHandler.END
        context.user_data.clear()
        context.user_data.update(
            report_category=category,
            report_category_key=category,
            category=REPORT_CATEGORIES[category],
            violation_type=None,
            post_link="",
            photo_path="",
            photo_file_id=None,
            place="",
            latitude=None,
            longitude=None,
            contact_phone="",
        )
        return await _show_report_step(update, context, ASK_TYPE)

    if parts[1] == "platform" and len(parts) == 3:
        platform_key = parts[2]
        if platform_key not in SOCIAL_PLATFORM_LABELS:
            await query.edit_message_text(
                texts.INLINE_MESSAGES["unknown_platform"],
                reply_markup=back_to_menu_kb(),
            )
            _remember_user_prompt(context, query.message)
            return ConversationHandler.END
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
        category = context.user_data.get("report_category_key")
        grouped_types = await db.effective_vtype_groups()
        available_types = grouped_types.get(category, [])
        if vtype not in available_types:
            await query.edit_message_text(
                texts.INLINE_MESSAGES["violation_type_unavailable"],
                reply_markup=report_category_kb(),
            )
            _remember_user_prompt(context, query.message)
            return ASK_CATEGORY
        context.user_data["violation_type"] = vtype
        next_step = ASK_PLATFORM if category == "social" else ASK_DESC
        return await _show_report_step(update, context, next_step)

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
            reply_markup=main_menu_kb(update.effective_user.id if update.effective_user else None),
        )
        _remember_user_prompt(context, query.message)
        return ConversationHandler.END

    logger.warning("استدعاء مستخدم غير معروف: %s", data)
    await query.edit_message_text(
        texts.UNKNOWN_BUTTON,
        reply_markup=back_to_menu_kb(),
    )
    _remember_user_prompt(context, query.message)


