"""
واجهة التحكم الإدارية (Admin API) — نقاط HTTPS محمية تخوّل المشرف تحكمًا كاملًا.

تُستدعى من:
  • تطبيق سطح المكتب (PySide6) الذي أعددناه لجهاز المشرف، و
  • لوحة الويب المنشورة على نفس المساحة، و
  • أي أداة تشغيلية مستقبلية (أوامر آلية، تقارير مجدولة…).

الحماية:
  1) سرّ ADMIN_API_TOKEN في أسرار المساحة — إن غاب فالمسار كله مغلق (503).
  2) كل طلب يحمَل ترويسة Authorization: Bearer ... أو X-Admin-Token
     ويقارَن الرمز بمقارنة زمنية ثابتة. مسارات الملفات تقبل Bearer فقط.
  3) لا يجدُد للعلن: كل عملية تُسجَّل في audit_log (من فعل ماذا ومتى).

مبدأ مصمَّم ببساطة: أي عملية تلمس قاعدة البيانات تمر عبر Database (نفس طبقة
البوت أخذًا للأقفال والتدقيق)، وأي إرسال لتلغرام يُنفَّذ على حلقة البوت نفسها
(cross-thread عبر run_coroutine_threadsafe) حتى لا تختلط حلقات uvicorn/PTB.

كل النقاط JSON وتعيد {"ok": true/false, ...} باستثناء مسار التصدير (ملف).
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import ipaddress
import json
import logging
import mimetypes
import os
import secrets
import time
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse  # noqa: E402

from bot import keyboards, texts  # noqa: E402
from services import (  # noqa: E402
    chat_attachments,
    utils,
    verified_reporters,
    word_filter,
)
from services.report_taxonomy import REPORT_CATEGORIES
from database import (
    STATUS_AWAITING_INFO,
    STATUS_CLOSED_NO_RESPONSE,
    STATUS_APPROVED,
    STATUS_CODES,
    STATUS_NEW,
    STATUS_REJECTED,
    STATUS_REVIEW,
)

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 100


def _main_module():
    return importlib.import_module("app.main")
DEFAULT_PAGE_SIZE = 20
MAX_TEXT = 2000
from api.admin_auth import (  # noqa: E402
    ADMIN_MAX_SESSIONS,
    ADMIN_PASSWORD_ITERATIONS,
    ADMIN_PERMISSIONS,
    ADMIN_SESSION_TTL_SECONDS,
    FAR_FUTURE,
    ROLE_PERMISSIONS,
    _admin_account_label,
    _admin_actor,
    _auth,
    _environment_owner_configured,
    _owner_only,
    _password_credentials,
    _password_matches,
    _token_digest,
    expected_token,
    token_ok,
)



def _audit_date_range(start: str, end: str) -> tuple[str, str] | None:
    try:
        start_day = datetime.strptime(start, "%Y-%m-%d").date()
        end_day = datetime.strptime(end, "%Y-%m-%d").date()
    except ValueError:
        return None
    if end_day < start_day:
        return None
    start_at = datetime.combine(start_day, datetime.min.time(), tzinfo=timezone.utc)
    end_at = datetime.combine(
        end_day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
    )
    return (
        start_at.isoformat(timespec="seconds"),
        end_at.isoformat(timespec="seconds"),
    )


def _sanitize_public_source_url(value: str) -> str | None:
    try:
        parsed = urllib.parse.urlsplit(value.strip())
        hostname = parsed.hostname
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not hostname
            or parsed.username
            or parsed.password
            or hostname.lower() == "localhost"
            or hostname.lower().endswith(".localhost")
            or "." not in hostname
        ):
            return None
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            return None
        port = parsed.port
        netloc = hostname.lower() + (f":{port}" if port else "")
        sanitized = urllib.parse.urlunsplit(
            (parsed.scheme.lower(), netloc, parsed.path[:1500], "", "")
        )
        return sanitized if len(sanitized) <= 1800 else None
    except (TypeError, ValueError):
        return None


# ==================================================================
# إشعار المتبلّغ (نفس آلية التكرار التي تستخدمها لوحة تيليغرام)
# ==================================================================
def _bot_runtime():
    """(bot, loop) إذا كان البوت حيًّا، أو (None, None)."""
    bot_main = _main_module()

    app = getattr(bot_main, "app", None)
    bot = getattr(app, "bot", None)
    if bot is not None:
        loop = app.bot_data.get("loop")
        if loop is not None and loop.is_running():
            return bot, loop
    return None, None


async def _on_bot_loop(coro_factory) -> bool:
    """ينفذ إنشاءًا متأخرًا على حلقة البوت ويعيد نتيجته منطقية بلا استثناء خارجي."""
    val = await _on_bot_loop_value(coro_factory)
    return bool(val)


async def _on_bot_loop_value(coro_factory):
    """ينفذ على حلقة البوت ويعيد القيمة كما هي (None عند غياب البوت/خطأ)."""
    bot, loop = _bot_runtime()
    if bot is None:
        return None
    fut = asyncio.run_coroutine_threadsafe(coro_factory(), loop)
    try:
        return await asyncio.wrap_future(fut)
    except Exception:
        logger.warning("فشل تنفيذ إجراء على حلقة البوت", exc_info=True)
        return None


async def _set_telegram_bot_profile(
    bot, profile: dict[str, str], photo_bytes: bytes | None = None
) -> dict[str, Any]:
    updates = (
        ("name", "get_my_name", "set_my_name", "name"),
        ("short_description", "get_my_short_description",
         "set_my_short_description", "short_description"),
        ("description", "get_my_description",
         "set_my_description", "description"),
    )
    for key, getter_name, setter_name, attribute in updates:
        setter = getattr(bot, setter_name, None)
        if setter is None:
            return {"ok": False, "error": "telegram_profile_method_unavailable"}
        current = None
        getter = getattr(bot, getter_name, None)
        if getter is not None:
            result = await getter()
            current = getattr(result, attribute, None)
        if current == profile[key]:
            continue
        if not await setter(**{key: profile[key]}):
            return {"ok": False, "error": "telegram_profile_update_rejected"}

    if photo_bytes:
        photo_setter = getattr(bot, "set_my_profile_photo", None)
        try:
            from telegram import InputProfilePhotoStatic

            photo = InputProfilePhotoStatic(photo_bytes)
        except ImportError:
            return {"ok": False, "error": "telegram_photo_update_unavailable"}
        if photo_setter is not None:
            accepted = await photo_setter(photo=photo)
        else:
            post_method = getattr(bot, "_post", None)
            if post_method is None:
                return {"ok": False, "error": "telegram_photo_update_unavailable"}
            accepted = await post_method(
                "setMyProfilePhoto", data={"photo": photo}
            )
        if not accepted:
            return {"ok": False, "error": "telegram_photo_update_rejected"}
    return {"ok": True}


async def _bot_ready() -> bool:
    return _bot_runtime()[0] is not None


async def _apply_bot_text_update(key: str, content: str) -> bool:
    return texts.apply_database_text(key, content)


async def _photo_bytes(bot_file_id: str):
    """يجلب بايتات صورة من تلغرام على حلقة البوت (None عند فشل/غياب)."""
    async def _getter():
        bot, loop = _bot_runtime()
        if bot is None:
            return None
        try:
            tg_file = await bot.get_file(bot_file_id)
            buf = bytearray()
            await tg_file.download_as_bytearray(buf)
            return bytes(buf)
        except Exception:
            logger.info("تعذر جلب صورة البلاغ من تلغرام", exc_info=True)
            return None

    return await _on_bot_loop_value(_getter)


def _image_media_type(data: bytes, filename: str = "") -> str:
    """Use image signatures before filename hints so PNGs/documents aren't mislabeled JPEG."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    guessed, _encoding = mimetypes.guess_type(filename)
    return guessed if guessed and guessed.startswith("image/") else "application/octet-stream"


def _parse_html():
    from telegram.constants import ParseMode  # noqa: PLC0415

    return ParseMode.HTML


PUBLICATION_CATEGORY_LABELS = {
    "security": "أمني",
    "public": "عام",
    "important": "هام",
}


def _publication_delivery_text(
    caption: str, media_type: str, category: str = "public"
) -> str:
    category_label = PUBLICATION_CATEGORY_LABELS.get(category, "عام")
    if media_type:
        return f"📨 منشور {category_label} من الجهة المختصة\n\n{caption}".strip()
    content = f"منشور {category_label}\n\n{caption}"
    return texts.BROADCAST_NOTIF.format(text=utils.esc(content))


# ==================================================================
# إشعار المتبلّغ (نفس آلية التكرار التي تستخدمها لوحة تيليغرام)
# ==================================================================
async def _notify_status(r: dict[str, Any], new_status: str, note_extra: str) -> bool:
    async def _sender():
        bot, loop = _bot_runtime()
        if bot is None:
            return False
        try:
            await bot.send_message(
                chat_id=int(r["user_id"]),
                text=_status_text(r, new_status, note_extra),
                parse_mode=_parse_html(),
            )
            return True
        except Exception:
            logger.info("لم يصل إشعار الحالة للمستخدم (ربما حجب البوت)", exc_info=True)
            return False

    return await _on_bot_loop(_sender)


async def _send_panel_reply(r: dict[str, Any], reply_text: str) -> bool:
    async def _sender():
        bot, loop = _bot_runtime()
        if bot is None:
            return False
        try:
            await bot.send_message(
                chat_id=int(r["user_id"]),
                text=texts.ADMIN_REPLY_TO_REPORTER.format(
                    seq=utils.esc(r.get("seq") or "-"), text=utils.esc(reply_text)
                ),
                parse_mode=_parse_html(),
            )
            return True
        except Exception:
            logger.info("لم يصل رد الجهة للمستخدم (ربما حجب البوت)", exc_info=True)
            return False

    return await _on_bot_loop(_sender)


async def _notify_verification_decision(
    user_id: int, decision: str, rejection_reason: str | None = None
) -> bool:
    async def _sender():
        bot, _loop = _bot_runtime()
        if bot is None:
            return False
        if decision == "verified":
            message = (
                "✅ تم اعتماد طلب توثيقك كمراسل موثوق. "
                "اعتماد الحساب لا يعني اعتماد البلاغات تلقائيًا."
            )
        else:
            message = (
                "لم يُعتمد طلب توثيقك كمراسل موثوق.\n"
                f"السبب: {utils.esc(rejection_reason or '')}\n\n"
                "يمكنك معالجة السبب ثم إعادة التقديم من القائمة الرئيسية."
            )
        try:
            await bot.send_message(
                chat_id=int(user_id),
                text=message,
                parse_mode=_parse_html(),
            )
            return True
        except Exception:
            logger.info(
                "تعذر إيصال قرار توثيق المراسل إلى المستخدم %s",
                user_id,
                exc_info=True,
            )
            return False

    return await _on_bot_loop(_sender)


async def _notify_user_verification_supplement(
    user_id: int,
    requested_documents: list[str],
    message: str,
) -> bool:
    async def _sender():
        bot, _loop = _bot_runtime()
        if bot is None:
            return False
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        text = texts.format_inline_message(
            "verification_supplement_notification",
            documents="\n".join(
                f"• {texts.VERIFICATION_DOCUMENT_LABELS[kind]}"
                for kind in requested_documents
            ),
            message=utils.esc(message),
        )
        try:
            await bot.send_message(
                chat_id=int(user_id),
                text=text,
                parse_mode=_parse_html(),
                reply_markup=InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton(
                            texts.INLINE_MESSAGES["verification_supplement_button"],
                            callback_data="usr:verify-supplement",
                        )
                    ]]
                ),
            )
            return True
        except Exception:
            logger.info(
                "Could not request verification supplement from user %s",
                user_id,
                exc_info=True,
            )
            return False

    return await _on_bot_loop(_sender)


async def _send_direct_message_result(
    user_id: int, text: str, reply_markup=None
) -> dict[str, Any]:
    async def _sender():
        bot, loop = _bot_runtime()
        if bot is None:
            return {"sent": False, "error_code": "bot_not_ready"}
        try:
            message = await bot.send_message(
                chat_id=int(user_id),
                text=texts.INLINE_MESSAGES["direct_admin_message"].format(
                    text=text
                ),
                reply_markup=reply_markup,
            )
            return {
                "sent": True,
                "message_id": getattr(message, "message_id", None),
            }
        except Exception as exc:
            from telegram.error import BadRequest, Forbidden, NetworkError  # noqa: PLC0415

            if isinstance(exc, Forbidden):
                error_code = "user_blocked_bot"
            elif isinstance(exc, BadRequest):
                error_code = "recipient_unavailable"
            elif isinstance(exc, NetworkError):
                error_code = "telegram_network_error"
            else:
                error_code = "telegram_send_failed"
            logger.info(
                "تعذر إرسال رسالة مباشرة للمستخدم (%s)",
                error_code,
                exc_info=True,
            )
            return {"sent": False, "error_code": error_code}

    result = await _on_bot_loop_value(_sender)
    return result if isinstance(result, dict) else {
        "sent": False,
        "error_code": "bot_not_ready",
    }


async def _send_direct_message(user_id: int, text: str, reply_markup=None) -> bool:
    result = await _send_direct_message_result(user_id, text, reply_markup)
    return result.get("sent") is True


async def _send_block_notice(user_id: int, kind: str, reason: str, db) -> bool:
    async def _sender():
        bot, loop = _bot_runtime()
        if bot is None:
            return False
        try:
            import guards  # noqa: PLC0415

            await bot.send_message(
                chat_id=int(user_id),
                text=await guards.render_block_message(
                    db, "manual_block", {"kind": kind, "reason": reason}
                ),
                parse_mode=_parse_html(),
            )
            return True
        except Exception:
            return False

    return await _on_bot_loop(_sender)


async def _send_unblock_notice(user_id: int) -> bool:
    async def _sender():
        bot, loop = _bot_runtime()
        if bot is None:
            return False
        try:
            await bot.send_message(
                chat_id=int(user_id),
                text=texts.INLINE_MESSAGES["account_unblocked"],
            )
            return True
        except Exception:
            logger.info("تعذر إرسال إشعار رفع الحظر للمستخدم", exc_info=True)
            return False

    return await _on_bot_loop(_sender)


def _status_text(r: dict[str, Any], new_status: str, note_extra: str) -> str:
    """نص إشعار الحالة (صيغة HTML مثل نصوص لوحة تيليغرام نفسها)."""
    from bot import admin as admin_mod  # noqa: PLC0415

    key = {STATUS_NEW: "new", STATUS_REVIEW: "review",
           STATUS_APPROVED: "approved", STATUS_REJECTED: "rejected",
           STATUS_AWAITING_INFO: "awaiting_info",
           STATUS_CLOSED_NO_RESPONSE: "closed_no_response"}.get(new_status)
    note = texts.STATUS_FINAL_LINES.get(key, "") if key else ""
    if note_extra:
        note = f"{note}{note_extra}"
    line = texts.STATUS_UPDATE_NOTIF.format(
        seq=r.get("seq") or "-",
        status=texts.badge(new_status),
        time=utils.fmt_local(r.get("reviewed_at") or utils.utc_now_iso()),
        note=note,
    )
    # دفتر التكرار المشترك مع لوحة تيليغرام: لا إشعار مكرر من أي جهة
    try:
        admin_mod._notified_status[int(r["id"])] = new_status
    except Exception:  # noqa: BLE001
        pass
    return line


# ==================================================================
# التسجيل: يضيف كل نقاط الواجهة إلى تطبيق FastAPI في space_app
# ==================================================================
from api.routers import notifications as notifications_router  # noqa: E402
from api.routers import reports as reports_router  # noqa: E402
from api.routers import bot_texts as bot_texts_router  # noqa: E402
from api.routers import admins as admins_router  # noqa: E402
from api.routers import verified_reporters as verified_router  # noqa: E402
from api.routers import audit as audit_router  # noqa: E402
from api.routers import osint as osint_router  # noqa: E402
from api.routers import users as users_router  # noqa: E402
from api.routers import broadcast as broadcast_router  # noqa: E402
from api.routers import bot_menu as bot_menu_router  # noqa: E402
from api.routers import bot_profile as bot_profile_router  # noqa: E402
from api.routers import platform as platform_router  # noqa: E402
from api.routers import mobile_app as mobile_app_router  # noqa: E402
def register(app) -> None:  # noqa: ANN001 — FastAPI
    OK = JSONResponse({"ok": True})

    def bad(msg: str, code: int = 400) -> JSONResponse:
        return JSONResponse({"ok": False, "error": msg}, status_code=code)

    def db_of() -> Any:  # noqa: ANN202
        bot_main = _main_module()
        return bot_main.app.bot_data["db"]

    app.state.admin_db_provider = db_of

    @app.middleware("http")
    async def load_admin_auth_state(request: Request, call_next):
        if request.url.path.startswith("/api/admin/"):
            if request.headers.get("x-admin-client", "").lower() == "android":
                from api.routers.mobile_app import load_mobile_policy

                policy = await load_mobile_policy(db_of())
                if policy["status"] != "active":
                    return JSONResponse(
                        {
                            "ok": False,
                            "error": (
                                "mobile_app_paused"
                                if policy["status"] == "paused"
                                else "mobile_app_disabled"
                            ),
                            "message": policy["status_message"],
                        },
                        status_code=503,
                    )
            try:
                request.state.admin_auth_snapshot = await asyncio.to_thread(
                    db_of().admin_auth_snapshot
                )
            except Exception:  # noqa: BLE001
                logger.exception("Could not load persisted admin credentials")
                request.state.admin_auth_unavailable = True
        return await call_next(request)

    platform_router.register(app, db_of=db_of, bad=bad)
    notifications_router.register(
        app,
        db_of=db_of,
        bad=bad,
        auth=_auth,
        owner_only=_owner_only,
        admin_actor=_admin_actor,
    )
    admins_router.register(app, db_of=db_of, bad=bad)
    reports_router.register(
        app,
        db_of=db_of,
        bad=bad,
        auth=_auth,
        json_body=_json,
        safe_report=_safe_report,
        admin_actor=_admin_actor,
        notify_status=_notify_status,
        send_panel_reply=_send_panel_reply,
        ok_response=OK,
    )
    bot_texts_router.register(
        app,
        db_of=db_of,
        bad=bad,
        auth=_auth,
        json_body=_json,
        admin_actor=_admin_actor,
        owner_only=_owner_only,
    )
    users_router.register(app, db_of=db_of, bad=bad)
    verified_router.register(app, db_of=db_of, bad=bad)
    osint_router.register(app, db_of=db_of, bad=bad)
    audit_router.register(app, db_of=db_of, bad=bad)
    broadcast_router.register(app, db_of=db_of, bad=bad)
    bot_menu_router.register(app, db_of=db_of, bad=bad)
    bot_profile_router.register(app, db_of=db_of, bad=bad)
    mobile_app_router.register(app, db_of=db_of, bad=bad)
# ==================================================================
# أدوات مساعدة
# ==================================================================
def _safe_report(r: dict[str, Any]) -> dict[str, Any]:
    """نسخة صالحة للعرض عبر الإنترنت (الصورة تُسحب من تلغرام عبر file_id)."""
    safe = {k: v for k, v in r.items() if k != "photo_path"}
    local = _local_photo_path(str(r.get("photo_path") or "").strip())
    safe["has_photo"] = bool(
        r.get("photo_file_id") or (local is not None and local.is_file())
    )
    return safe


def _local_photo_path(raw_path: str):
    if not raw_path:
        return None
    from pathlib import Path  # noqa: PLC0415

    path = Path(raw_path)
    if not path.is_absolute():
        from app import config  # noqa: PLC0415

        path = config.BASE_DIR / path
    return path


async def _json(request) -> dict[str, Any]:
    """يقرأ جسم الطلب JSON بأمان (فارغ عند الخطأ)."""
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}
