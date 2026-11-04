"""تدفق توثيق المراسلين.

نُقلت من ``bot/handlers.py`` مع إعادة استيراد أسمائها هناك، فلا يتغير
أي تسجيل أو استدعاء.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
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
from bot.helpers import (
    ACTIVE_PROMPT_KEY,
    ASK_VERIFICATION_ID_BACK,
    ASK_VERIFICATION_ID_FRONT,
    ASK_VERIFICATION_PHONE,
    ASK_VERIFICATION_SELFIE,
    ASK_VERIFICATION_SUPPLEMENT,
    VERIFICATION_CAPTURES_KEY,
    VERIFICATION_STEP_KEY,
    VERIFICATION_TICKET_KEY,
    VERIFICATION_TICKET_MINUTES,
    _db,
    _send_clean_prompt,
    _typing_while_processing,
)
from bot.keyboards import main_menu_kb
from database import Database
from services import guards, identity_image_checks, sightengine_moderation, utils, verified_reporters


async def _verification_consent(db: Database) -> tuple[str, str]:
    """غلاف يربط الاسم وقت الطلب من ``bot.handlers``.

    يبقي الدالة قابلة للاستبدال في الاختبارات عبر ``patch`` على
    ``bot.handlers`` ويمنع الاستيراد الدائري.
    """
    from bot import handlers

    return await handlers._verification_consent(db)

logger = logging.getLogger(__name__)


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
            reply_markup=main_menu_kb(update.effective_user.id if update.effective_user else None),
        )
        return ConversationHandler.END
    if not verified_reporters.is_configured():
        await _send_clean_prompt(
            update,
            context,
            texts.INLINE_MESSAGES["verification_disabled"],
            reply_markup=main_menu_kb(update.effective_user.id if update.effective_user else None),
        )
        context.user_data.clear()
        return ConversationHandler.END

    contact = message.contact
    if not contact or contact.user_id != user.id:
        await _send_clean_prompt(
            update,
            context,
            texts.INLINE_MESSAGES["verification_share_own_phone"],
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
            texts.INLINE_MESSAGES["verification_syrian_phone_only"],
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
            texts.INLINE_MESSAGES["verification_storage_unavailable"],
            reply_markup=main_menu_kb(update.effective_user.id if update.effective_user else None),
        )
        context.user_data.clear()
        return ConversationHandler.END

    if request_id is None:
        previous = await _db(context).verification_status(user.id)
        if previous and previous.get("status") == "verified":
            message_text = texts.INLINE_MESSAGES["verification_already_verified"]
        elif previous and previous.get("status") == "pending":
            message_text = texts.INLINE_MESSAGES["verification_already_pending"]
        else:
            message_text = texts.INLINE_MESSAGES["verification_request_open"]
        context.user_data.clear()
        await _send_clean_prompt(
            update,
            context,
            message_text,
            reply_markup=main_menu_kb(update.effective_user.id if update.effective_user else None),
        )
        return ConversationHandler.END

    context.user_data[VERIFICATION_TICKET_KEY] = ticket_hash
    context.user_data[VERIFICATION_CAPTURES_KEY] = {}
    context.user_data[VERIFICATION_STEP_KEY] = "selfie"
    await _send_clean_prompt(
        update,
        context,
        texts.INLINE_MESSAGES["verification_selfie_prompt"],
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
    key = f"verification_{code}"
    return texts.INLINE_MESSAGES.get(
        key, texts.INLINE_MESSAGES["verification_image_check_failed"]
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
            texts.INLINE_MESSAGES["verification_session_expired"],
            reply_markup=main_menu_kb(update.effective_user.id if update.effective_user else None),
        )
        return ConversationHandler.END
    try:
        _, current_consent_version = await _verification_consent(_db(context))
    except Exception:
        logger.exception("Could not verify consent version for user %s", user.id)
        await message.reply_text(
            texts.INLINE_MESSAGES["verification_privacy_unavailable"]
        )
        return _verification_state_for_kind(kind)
    if context.user_data.get("verification_consent_version") != current_consent_version:
        await _discard_verification_capture(context, user.id)
        context.user_data.pop("verification_consent_at", None)
        context.user_data.pop("verification_consent_version", None)
        await _send_clean_prompt(
            update,
            context,
            texts.INLINE_MESSAGES["verification_privacy_changed"],
            reply_markup=main_menu_kb(update.effective_user.id if update.effective_user else None),
        )
        return ConversationHandler.END

    if not message.photo:
        await message.reply_text(
            texts.INLINE_MESSAGES["verification_image_format"],
        )
        return _verification_state_for_kind(kind)
    media = message.photo[-1]
    if media.file_size is not None and (
        media.file_size <= 0
        or media.file_size > identity_image_checks.MAX_IMAGE_BYTES
    ):
        await message.reply_text(_verification_image_error_message("image_size_invalid"))
        return _verification_state_for_kind(kind)

    try:
        async with _typing_while_processing(context.bot, user.id):
            telegram_file = await context.bot.get_file(media.file_id)
            buffer = bytearray()
            await telegram_file.download_as_bytearray(buffer)
            image_bytes = bytes(buffer)
            await asyncio.to_thread(
                identity_image_checks.validate_identity_image,
                kind,
                image_bytes,
            )
            if kind == "selfie":
                if not sightengine_moderation.credentials():
                    raise sightengine_moderation.SightengineError(
                        "Sightengine credentials are not configured"
                    )
                with tempfile.NamedTemporaryFile(
                    prefix="mrw-verification-", suffix=".jpg", delete=False
                ) as handle:
                    inspection_path = Path(handle.name)
                try:
                    inspection_path.write_bytes(image_bytes)
                    scores = await sightengine_moderation.inspect_image(
                        inspection_path
                    )
                finally:
                    try:
                        inspection_path.unlink(missing_ok=True)
                    except OSError:
                        logger.exception(
                            "Could not remove temporary verification image for user %s",
                            user.id,
                        )
                if scores.is_restricted:
                    raise identity_image_checks.IdentityImageCheckError(
                        "inappropriate_content"
                    )
            encrypted = await asyncio.to_thread(
                verified_reporters.encrypt_document,
                kind,
                image_bytes,
            )
    except identity_image_checks.IdentityImageCheckError as exc:
        await message.reply_text(_verification_image_error_message(str(exc)))
        return _verification_state_for_kind(kind)
    except sightengine_moderation.SightengineError:
        logger.exception(
            "Sightengine could not screen verification selfie for user %s",
            user.id,
        )
        await message.reply_text(
            texts.INLINE_MESSAGES["verification_sightengine_failed"]
        )
        return _verification_state_for_kind(kind)
    except verified_reporters.VerificationUnavailable:
        logger.exception("Identity verification encryption became unavailable")
        await _discard_verification_capture(context, user.id)
        await _send_clean_prompt(
            update,
            context,
            texts.INLINE_MESSAGES["verification_encryption_failed"],
            reply_markup=main_menu_kb(update.effective_user.id if update.effective_user else None),
        )
        return ConversationHandler.END
    except Exception:
        logger.exception(
            "Unexpected verification image processing failure for user %s",
            user.id,
        )
        await message.reply_text(
            texts.INLINE_MESSAGES["verification_processing_failed"]
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
            texts.INLINE_MESSAGES["verification_ticket_save_failed"]
        )
        return ConversationHandler.END
    if not ticket_extended:
        await _discard_verification_capture(context, user.id)
        await _send_clean_prompt(
            update,
            context,
            texts.INLINE_MESSAGES["verification_session_expired"],
            reply_markup=main_menu_kb(update.effective_user.id if update.effective_user else None),
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
            "id_front": texts.INLINE_MESSAGES["verification_face_passed_next_id"],
            "id_back": texts.INLINE_MESSAGES["verification_image_passed_next_back"],
        }
        await _send_clean_prompt(update, context, prompts[next_kind])
        return next_state

    if set(captures) != verified_reporters.DOCUMENT_KINDS:
        logger.error("Incomplete verified-reporter image sequence for user %s", user.id)
        await _discard_verification_capture(context, user.id)
        await _send_clean_prompt(
            update,
            context,
            texts.INLINE_MESSAGES["verification_image_sequence_invalid"],
            reply_markup=main_menu_kb(update.effective_user.id if update.effective_user else None),
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
            texts.INLINE_MESSAGES["verification_save_failed"],
            reply_markup=main_menu_kb(update.effective_user.id if update.effective_user else None),
        )
        return ConversationHandler.END
    if request_id is None:
        await _discard_verification_capture(context, user.id)
        await _send_clean_prompt(
            update,
            context,
            texts.INLINE_MESSAGES["verification_submission_session_expired"],
            reply_markup=main_menu_kb(update.effective_user.id if update.effective_user else None),
        )
        return ConversationHandler.END

    active_prompt = context.user_data.get(ACTIVE_PROMPT_KEY)
    context.user_data.clear()
    if active_prompt:
        context.user_data[ACTIVE_PROMPT_KEY] = active_prompt
    await _send_clean_prompt(
        update,
        context,
        texts.INLINE_MESSAGES["verification_submission_complete"],
        reply_markup=main_menu_kb(update.effective_user.id if update.effective_user else None),
    )
    return ConversationHandler.END

async def _receive_verification_supplement_image(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat or chat.type != "private":
        return ConversationHandler.END
    request_id = context.user_data.get("verification_supplement_request_id")
    if not isinstance(request_id, int):
        await message.reply_text(texts.INLINE_MESSAGES["supplement_start_required"])
        return ConversationHandler.END
    if not message.photo:
        await message.reply_text(texts.INLINE_MESSAGES["supplement_image_required"])
        return ASK_VERIFICATION_SUPPLEMENT
    try:
        supplement = await _db(context).active_verification_supplement(
            request_id, user.id
        )
    except Exception:
        logger.exception("Could not load verification supplement for user %s", user.id)
        await message.reply_text(texts.INLINE_MESSAGES["supplement_load_failed"])
        return ASK_VERIFICATION_SUPPLEMENT
    if not supplement:
        context.user_data.pop("verification_supplement_request_id", None)
        await message.reply_text(
            texts.INLINE_MESSAGES["supplement_missing"]
        )
        return ConversationHandler.END
    requested = supplement["requested_documents"]
    uploaded = set(supplement["uploaded_documents"])
    remaining = [kind for kind in requested if kind not in uploaded]
    if not remaining:
        context.user_data.pop("verification_supplement_request_id", None)
        return ConversationHandler.END
    kind = remaining[0]
    media = message.photo[-1]
    if media.file_size is not None and (
        media.file_size <= 0 or media.file_size > identity_image_checks.MAX_IMAGE_BYTES
    ):
        await message.reply_text(_verification_image_error_message("image_size_invalid"))
        return ASK_VERIFICATION_SUPPLEMENT
    try:
        async with _typing_while_processing(context.bot, user.id):
            telegram_file = await context.bot.get_file(media.file_id)
            buffer = bytearray()
            await telegram_file.download_as_bytearray(buffer)
            image_bytes = bytes(buffer)
            await asyncio.to_thread(
                identity_image_checks.validate_identity_image,
                kind,
                image_bytes,
            )
            if kind == "selfie":
                if not sightengine_moderation.credentials():
                    raise sightengine_moderation.SightengineError(
                        "Sightengine credentials are not configured"
                    )
                with tempfile.NamedTemporaryFile(
                    prefix="mrw-verification-supplement-", suffix=".jpg",
                    delete=False,
                ) as handle:
                    inspection_path = Path(handle.name)
                try:
                    inspection_path.write_bytes(image_bytes)
                    scores = await sightengine_moderation.inspect_image(
                        inspection_path
                    )
                finally:
                    inspection_path.unlink(missing_ok=True)
                if scores.is_restricted:
                    raise identity_image_checks.IdentityImageCheckError(
                        "inappropriate_content"
                    )
            encrypted = await asyncio.to_thread(
                verified_reporters.encrypt_document,
                kind,
                image_bytes,
            )
        saved = await _db(context).save_verification_supplement_upload(
            request_id,
            user.id,
            kind,
            encrypted,
        )
    except identity_image_checks.IdentityImageCheckError as exc:
        await message.reply_text(_verification_image_error_message(str(exc)))
        return ASK_VERIFICATION_SUPPLEMENT
    except sightengine_moderation.SightengineError:
        logger.exception(
            "Sightengine could not screen supplement selfie for user %s", user.id
        )
        await message.reply_text(
            texts.INLINE_MESSAGES["supplement_sightengine_failed"]
        )
        return ASK_VERIFICATION_SUPPLEMENT
    except verified_reporters.VerificationUnavailable:
        logger.exception("Could not encrypt identity supplement for user %s", user.id)
        await message.reply_text(texts.INLINE_MESSAGES["supplement_save_failed"])
        return ASK_VERIFICATION_SUPPLEMENT
    except ValueError as exc:
        logger.info("Rejected verification supplement for user %s: %s", user.id, exc)
        await message.reply_text(texts.INLINE_MESSAGES["supplement_wrong_image"])
        return ASK_VERIFICATION_SUPPLEMENT
    except Exception:
        logger.exception("Could not save verification supplement for user %s", user.id)
        await message.reply_text(texts.INLINE_MESSAGES["supplement_upload_failed"])
        return ASK_VERIFICATION_SUPPLEMENT
    if not saved:
        await message.reply_text(
            texts.INLINE_MESSAGES["supplement_expired"]
        )
        return ConversationHandler.END
    if not saved["completed"]:
        next_kind = saved["remaining_documents"][0]
        await message.reply_text(
            texts.INLINE_MESSAGES["supplement_followup_image"]
            + texts.VERIFICATION_DOCUMENT_LABELS[next_kind]
        )
        return ASK_VERIFICATION_SUPPLEMENT

    context.user_data.pop("verification_supplement_request_id", None)
    db = _db(context)
    dashboard_notified = False
    try:
        await db.create_notification(
            "verification_supplement_received",
            {
                "request_id": request_id,
                "user_id": user.id,
                "dashboard_path": f"/verification/{request_id}",
            },
        )
        dashboard_notified = True
    except Exception:
        logger.exception(
            "Could not create dashboard notification for supplement %s",
            request_id,
        )
    await _notify_verification_supplement_reviewers(
        context, request_id, user.id, saved["requested_documents"]
    )
    await db.audit(
        user.id,
        "verified_reporter_supplement_received",
        str(request_id),
        f"documents={','.join(saved['requested_documents'])}; "
        f"dashboard_notification={dashboard_notified}",
    )
    await message.reply_text(
        texts.INLINE_MESSAGES["verification_upload_complete"]
    )
    return ConversationHandler.END


async def _notify_verification_supplement_reviewers(
    context: ContextTypes.DEFAULT_TYPE,
    request_id: int,
    user_id: int,
    kinds: list[str],
) -> bool:
    db = _db(context)
    try:
        accounts, _ = await asyncio.to_thread(db.admin_auth_snapshot)
    except Exception:
        logger.exception("Could not resolve permitted identity reviewers")
        return False
    recipients: set[int] = set()
    fallback_permissions = {
        "viewer": {"read"},
        "reviewer": {"read", "review"},
        "operator": {"read", "review", "users", "operate", "export"},
        "owner": {
            "read", "review", "users", "operate", "export", "delete", "identity",
        },
    }

    for account in accounts:
        if not isinstance(account, dict) or account.get("active") is not True:
            continue
        admin_id = account.get("admin_id")
        if not isinstance(admin_id, int) or admin_id <= 0:
            continue
        permissions = account.get("permissions")
        if permissions is None:
            permissions = fallback_permissions.get(account.get("role"), set())
        if (
            isinstance(permissions, (list, tuple, set))
            and "identity_documents" in permissions
        ):
            recipients.add(admin_id)
    if not recipients:
        return False
    documents: list[tuple[str, bytes]] = []
    try:
        for kind in kinds:
            ciphertext = await db.verification_document(request_id, kind)
            if not ciphertext:
                return False
            documents.append(
                (kind, verified_reporters.decrypt_document(ciphertext))
            )
    except verified_reporters.VerificationUnavailable:
        logger.exception("Could not decrypt received identity supplement")
        return False
    delivered = False
    import io

    for recipient in sorted(recipients):
        complete = True
        for kind, content in documents:
            try:
                await context.bot.send_photo(
                    chat_id=recipient,
                    photo=io.BytesIO(content),
                    caption=texts.INLINE_MESSAGES[
                        "verification_supplement_caption"
                    ].format(
                        request_id=request_id,
                        user_id=user_id,
                        kind=kind,
                    ),
                )
            except Exception:
                logger.info(
                    "Could not send supplement to authorized reviewer %s",
                    recipient,
                    exc_info=True,
                )
                complete = False
                break
        if not complete:
            try:
                await context.bot.send_message(
                    chat_id=recipient,
                    text=texts.INLINE_MESSAGES[
                        "verification_supplement_fallback"
                    ].format(request_id=request_id),
                )
            except Exception:
                logger.info(
                    "Could not send protected-link fallback to reviewer %s",
                    recipient,
                    exc_info=True,
                )
        delivered = delivered or complete
    return delivered

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


