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
from api.routers import bot_config as bot_config_router  # noqa: E402
def register(app) -> None:  # noqa: ANN001 — FastAPI
    OK = JSONResponse({"ok": True})

    def bad(msg: str, code: int = 400) -> JSONResponse:
        return JSONResponse({"ok": False, "error": msg}, status_code=code)

    @app.get("/version")
    async def version() -> JSONResponse:
        return JSONResponse({
            "ok": True,
            "commit": os.getenv("RENDER_GIT_COMMIT", "").strip() or "unknown",
            "environment": (
                os.getenv("RENDER_ENVIRONMENT", "").strip()
                or os.getenv("RENDER_SERVICE_NAME", "").strip()
                or "unknown"
            ),
        })

    def db_of() -> Any:  # noqa: ANN202
        bot_main = _main_module()
        return bot_main.app.bot_data["db"]

    app.state.admin_db_provider = db_of

    @app.middleware("http")
    async def load_admin_auth_state(request: Request, call_next):
        if request.url.path.startswith("/api/admin/"):
            try:
                request.state.admin_auth_snapshot = await asyncio.to_thread(
                    db_of().admin_auth_snapshot
                )
            except Exception:  # noqa: BLE001
                logger.exception("Could not load persisted admin credentials")
                request.state.admin_auth_unavailable = True
        return await call_next(request)

    notifications_router.register(
        app,
        db_of=db_of,
        bad=bad,
        auth=_auth,
        owner_only=_owner_only,
        admin_actor=_admin_actor,
    )

    @app.get("/db-health")
    def db_health() -> JSONResponse:
        try:
            database = db_of()
            if database.health_check():
                return JSONResponse({"ok": True})
        except Exception:  # noqa: BLE001
            logger.error("Database health check failed")
        return JSONResponse(
            {"ok": False, "error": "database_unavailable"},
            status_code=503,
        )

    # ------------------------------------------------------------
    # 0) نظرة عامة
    # ------------------------------------------------------------
    admins_router.register(app, db_of=db_of, bad=bad)

    @app.get("/api/admin/overview")
    async def overview(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        db = db_of()
        counts = await db.count_by_status()
        last24 = await db.count_since(
            (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(
                timespec="seconds"
            )
        )
        emergency = await db.get_state("emergency") == "1"
        import guards  # noqa: PLC0415

        return JSONResponse({
            "ok": True,
            "counts": {
                STATUS_NEW: counts.get(STATUS_NEW, 0),
                STATUS_REVIEW: counts.get(STATUS_REVIEW, 0),
                STATUS_APPROVED: counts.get(STATUS_APPROVED, 0),
                STATUS_REJECTED: counts.get(STATUS_REJECTED, 0),
                "total": await db.total(),
                "last24": last24,
            },
            "emergency": emergency,
            "auto_freeze_paused_until": await guards.auto_freeze_pause_until(db),
            "bot_ready": await _bot_ready(),
            "workflow": await db.workflow_counts(),
            "users": {
                "total": await db.total_users(),
                "blocked": await db.blocked_users_count(),
            },
        })

    # ------------------------------------------------------------
    # 1) البلاغات: قائمة + تفصيل
    # ------------------------------------------------------------
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

    # ================================================================
    # endpoints إدارة النصوص
    # ================================================================
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

    @app.get("/api/admin/meta")
    async def meta(request: Request) -> JSONResponse:
        """عناصر ثابتة تحتاجها الواجهات: قائمة التصنيفات المعتمدة."""
        refuse = _auth(request)
        if refuse:
            return refuse
        from app import config  # noqa: PLC0415

        return JSONResponse({
            "ok": True,
            "vtypes": list(config.VIOLATION_TYPES),
            "admin_ids": config.ADMIN_IDS,
            "statuses": [
                STATUS_NEW,
                STATUS_REVIEW,
                STATUS_AWAITING_INFO,
                STATUS_APPROVED,
                STATUS_REJECTED,
                STATUS_CLOSED_NO_RESPONSE,
            ],
        })

    @app.get("/api/admin/photo/{rid}")
    async def photo(rid: int, request: Request) -> Any:
        """صورة البلاغ: محليًا إن وُجد وإلا من تلغرام (file_id). تعيد بايتات صورة."""
        refuse = _auth(request)
        if refuse:
            return refuse
        r = await db_of().get_by_id(rid)
        if not r:
            return bad("not_found", 404)
        from pathlib import Path as P  # noqa: PLC0415

        local_raw = str(r.get("photo_path") or "").strip()
        local = _local_photo_path(local_raw)
        download = request.query_params.get("download") == "true"
        if local is not None and local.is_file():
            media_type = mimetypes.guess_type(local.name)[0] or "application/octet-stream"
            if not media_type.startswith("image/"):
                media_type = "application/octet-stream"
            return FileResponse(
                str(local),
                media_type=media_type,
                filename=(
                    f"report-{rid}-photo{local.suffix.lower()}" if download else None
                ),
                headers={"Cache-Control": "private, no-store"},
            )
        file_id = r.get("photo_file_id")
        if not file_id:
            return bad("no_photo", 404)

        data = await _photo_bytes(file_id)
        if not data:
            return bad("photo_unavailable", 503)
        from fastapi.responses import Response  # noqa: PLC0415

        media_type = _image_media_type(data)
        headers = {"Cache-Control": "private, no-store"}
        if download:
            extension = mimetypes.guess_extension(media_type) or ".jpg"
            headers["Content-Disposition"] = (
                f'attachment; filename="report-{rid}-photo{extension}"'
            )
        return Response(
            content=data,
            media_type=media_type,
            headers=headers,
        )

    @app.get("/api/admin/reports/{rid}/download")
    async def download_report(rid: int, request: Request) -> Response:
        refuse = _auth(request)
        if refuse:
            return refuse
        item = await db_of().get_by_id(rid)
        if not item:
            return bad("not_found", 404)
        fields = (
            ("رقم البلاغ", item.get("seq") or item.get("id")),
            ("تاريخ الوصول", item.get("created_at")),
            ("المستخدم", item.get("user_id")),
            ("الحالة", item.get("status")),
            ("التصنيف", item.get("violation_type")),
            ("الوصف", item.get("description")),
            ("الرابط", item.get("post_link")),
            ("ملاحظة الإدارة", item.get("admin_note")),
        )
        content = "\n".join(f"{label}: {value or '—'}" for label, value in fields)
        return Response(
            content,
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="report-{rid}.txt"'
            },
        )

    # ------------------------------------------------------------
    # 5) تشغيل النظام: إيقاف طارئ/استئناف + تعميم + قوالب + تصنيفات
    # ------------------------------------------------------------
    @app.post("/api/admin/system/emergency")
    async def emergency_toggle(request: Request) -> JSONResponse:
        """الإيقاف الطارئ: يمنع استقبال البلاغات فورًا (نفس آلية غريق الكوارث في البوت)."""
        refuse = _auth(request)
        if refuse:
            return refuse
        body = await _json(request)
        on = body.get("on")
        if not isinstance(on, bool):
            return bad("bad_setting_value")
        reason = (str(body.get("reason") or "").strip())[:MAX_TEXT]
        if on and not reason:
            return bad("reason_required")
        import guards  # noqa: PLC0415

        db = db_of()
        await guards.set_emergency(
            db, on, _admin_actor(request), reason or ("استئناف من لوحة الإدارة")
        )
        return JSONResponse({"ok": True, "emergency": on})

    @app.post("/api/admin/system/user-limits")
    async def user_limits_toggle(request: Request) -> JSONResponse:
        """Temporarily pause per-user limits; global flood and emergency safety stay active."""
        refuse = _auth(request)
        if refuse:
            return refuse
        body = await _json(request)
        on = body.get("on")
        if not isinstance(on, bool):
            return bad("bad_setting_value")
        reason = (str(body.get("reason") or "").strip())[:MAX_TEXT]
        db = db_of()
        from app import config  # noqa: PLC0415
        import guards  # noqa: PLC0415

        if on:
            if not reason:
                return bad("reason_required")
            duration = body.get("duration_minutes")
            if isinstance(duration, bool):
                return bad("bad_setting_value")
            try:
                duration = int(duration)
            except (TypeError, ValueError):
                return bad("bad_setting_value")
            if not (
                config.USER_LIMITS_PAUSE_MINUTES_MIN
                <= duration
                <= config.USER_LIMITS_PAUSE_MINUTES_MAX
            ):
                return bad("setting_out_of_range")
            until = await guards.set_user_limits_pause(
                db, duration, _admin_actor(request), reason
            )
            return JSONResponse({"ok": True, "paused": True, "paused_until": until})

        await guards.resume_user_limits(db, _admin_actor(request), reason)
        return JSONResponse({"ok": True, "paused": False, "paused_until": None})

    @app.post("/api/admin/system/auto-freeze")
    async def auto_freeze_toggle(request: Request) -> JSONResponse:
        """Pause automatic user freezes for a bounded period; rate limits remain active."""
        refuse = _auth(request)
        if refuse:
            return refuse
        body = await _json(request)
        on = body.get("on")
        if not isinstance(on, bool):
            return bad("bad_setting_value")
        reason = (str(body.get("reason") or "").strip())[:MAX_TEXT]
        db = db_of()
        from app import config  # noqa: PLC0415
        import guards  # noqa: PLC0415

        if on:
            if not reason:
                return bad("reason_required")
            duration = body.get("duration_minutes")
            if isinstance(duration, bool):
                return bad("bad_setting_value")
            try:
                duration = int(duration)
            except (TypeError, ValueError):
                return bad("bad_setting_value")
            if not (
                config.AUTO_FREEZE_PAUSE_MINUTES_MIN
                <= duration
                <= config.AUTO_FREEZE_PAUSE_MINUTES_MAX
            ):
                return bad("setting_out_of_range")
            until = await guards.set_auto_freeze_pause(
                db, duration, _admin_actor(request), reason
            )
            return JSONResponse({
                "ok": True,
                "paused": True,
                "paused_until": until,
            })

        await guards.resume_auto_freeze(db, _admin_actor(request), reason)
        return JSONResponse({"ok": True, "paused": False, "paused_until": None})

    broadcast_router.register(app, db_of=db_of, bad=bad)

    @app.get("/api/admin/templates")
    async def templates_get(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        return JSONResponse({"ok": True, "items": await db_of().get_templates()})

    @app.put("/api/admin/templates")
    async def templates_put(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        body = await _json(request)
        items = body.get("items")
        if not isinstance(items, list):
            return bad("bad_items")
        clean = [str(x).strip() for x in items if str(x).strip()]
        if len(clean) > 30:
            return bad("too_many")
        if any(not 2 <= len(t) <= 300 for t in clean):
            return bad("item_length")
        await db_of().set_templates(clean)
        await db_of().audit(_admin_actor(request), "templates_changed", "القوالب",
                            f"عدد القوالب: {len(clean)}")
        return JSONResponse({"ok": True, "items": clean})

    @app.get("/api/admin/vtypes")
    async def vtypes_get(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        db = db_of()
        items = await db.effective_vtypes()
        return JSONResponse({
            "ok": True,
            "items": items,
            "categories": REPORT_CATEGORIES,
            "groups": await db.effective_vtype_groups(),
        })

    @app.put("/api/admin/vtypes")
    async def vtypes_put(request: Request) -> JSONResponse:
        """تعديل قائمة أنواع المخالفات نفسها — تتبدل أزرار إرسال البلاغ فورًا."""
        refuse = _auth(request)
        if refuse:
            return refuse
        body = await _json(request)
        items = body.get("items")
        if not isinstance(items, list):
            return bad("bad_items")
        clean, seen = [], set()
        for t in items:
            t = str(t).strip()[:40]
            if t and t not in seen:
                seen.add(t)
                clean.append(t)
        if not 3 <= len(clean) <= 24:
            return bad("bad_list")
        db = db_of()
        previous_items = await db.effective_vtypes()
        raw_groups = body.get("groups")
        if raw_groups is None:
            previous_groups = await db.effective_vtype_groups()
            groups = {
                category: [
                    item
                    for item in clean
                    if item in previous_groups[category]
                    or item not in previous_items
                ]
                for category in REPORT_CATEGORIES
            }
        else:
            if (
                not isinstance(raw_groups, dict)
                or set(raw_groups) != set(REPORT_CATEGORIES)
            ):
                return bad("bad_type_groups")
            groups = {}
            for category in REPORT_CATEGORIES:
                values = raw_groups[category]
                if not isinstance(values, list):
                    return bad("bad_type_groups")
                selected, selected_seen = [], set()
                for value in values:
                    if not isinstance(value, str) or value not in clean:
                        return bad("bad_type_groups")
                    if value not in selected_seen:
                        selected.append(value)
                        selected_seen.add(value)
                groups[category] = selected
        if any(not groups[category] for category in REPORT_CATEGORIES):
            return bad("empty_type_group")
        assigned = {item for values in groups.values() for item in values}
        if any(item not in assigned for item in clean):
            return bad("unassigned_types")
        await db.set_vtypes_override(clean)
        await db.set_vtype_groups(groups)
        await db.audit(_admin_actor(request), "vtypes_changed", "التصنيفات",
                       " / ".join(clean))
        return JSONResponse({
            "ok": True,
            "items": clean,
            "categories": REPORT_CATEGORIES,
            "groups": groups,
        })

    # ------------------------------------------------------------------
    # أزرار قائمة المستخدم — تعديل حي يطبَّق على البوت فورًا
    # ------------------------------------------------------------------
    bot_config_router.register(app, db_of=db_of, bad=bad)

    @app.get("/api/admin/system/health")
    async def system_health(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        db = db_of()
        try:
            database_ready = db.health_check()
            database_error = None if database_ready else "database_unavailable"
        except Exception:
            logger.exception("تعذر فحص قاعدة البيانات")
            database_ready, database_error = False, "database_unavailable"
        try:
            total = await db.total() if database_ready else 0
        except Exception:
            logger.exception("تعذر قراءة إجمالي البلاغات")
            total = 0
        bot_alive = False
        if database_ready:
            try:
                heartbeat = await db.get_state("bot_last_heartbeat")
                if heartbeat:
                    heartbeat_at = datetime.fromisoformat(
                        heartbeat.replace("Z", "+00:00")
                    )
                    if heartbeat_at.tzinfo is None:
                        heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
                    age_seconds = (
                        datetime.now(timezone.utc) - heartbeat_at
                    ).total_seconds()
                    bot_alive = 0 <= age_seconds < 180
            except (TypeError, ValueError):
                logger.warning("قيمة نبضة حياة البوت غير صالحة")
            except Exception as exc:
                logger.error(
                    "تعذرت قراءة نبضة حياة البوت (%s)", type(exc).__name__
                )
        import hub_backup  # noqa: PLC0415

        bot_main = _main_module()

        return JSONResponse({
            "ok": True,
            "bot_ready": await _bot_ready(),
            "bot_alive": bot_alive,
            "webhook_ready": bool(
                getattr(bot_main, "app", None)
                and bot_main.app.bot_data.get("webhook_ready")
            ),
            "database_ready": database_ready,
            "backend": db.backend,
            "database_error": database_error,
            "reports_total": total,
            "backup_configured": hub_backup.is_configured(),
            "backup_last_attempt_at": await db.get_state("backup_last_attempt_at"),
            "backup_last_success_at": await db.get_state("backup_last_success_at"),
            "backup_last_status": await db.get_state("backup_last_status"),
            "emergency": await db.get_state("emergency") == "1",
        })

    @app.get("/api/admin/system/integrations")
    async def system_integrations(request: Request) -> JSONResponse:
        """Read-only snapshot of this deployment and public GitHub activity."""
        refuse = _auth(request)
        if refuse:
            return refuse
        db = db_of()
        try:
            database_ready = bool(db.health_check())
        except Exception:
            database_ready = False
            logger.warning("تعذر فحص قاعدة البيانات لملخص التكاملات", exc_info=True)
        try:
            heartbeat = await db.get_state("bot_last_heartbeat")
            heartbeat_at = (
                datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
                if heartbeat
                else None
            )
            if heartbeat_at and heartbeat_at.tzinfo is None:
                heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
            bot_alive = bool(
                heartbeat_at
                and 0 <= (
                    datetime.now(timezone.utc) - heartbeat_at
                ).total_seconds() < 180
            )
        except (TypeError, ValueError):
            bot_alive = False
        except Exception:
            logger.warning("تعذرت قراءة نبضة البوت للتكاملات", exc_info=True)
            bot_alive = False

        import hub_backup  # noqa: PLC0415
        import integrations_monitor  # noqa: PLC0415

        backup_configured = hub_backup.is_configured()
        backup_status = await db.get_state("backup_last_status")
        github = await asyncio.to_thread(integrations_monitor.github_activity)
        bot_ready = await _bot_ready()
        services = [
            {
                "id": "render",
                "name": "خدمة الاستضافة الحالية",
                "status": "healthy",
                "detail": "تخدم هذا الطلب الآن",
            },
            {
                "id": "database",
                "name": "قاعدة البيانات",
                "status": "healthy" if database_ready else "degraded",
                "detail": db.backend,
            },
            {
                "id": "telegram",
                "name": "البوت والنبض",
                "status": (
                    "healthy" if bot_ready and bot_alive else "degraded"
                ),
                "detail": "نبضة حديثة" if bot_alive else "لا توجد نبضة حديثة",
            },
            {
                "id": "backup",
                "name": "النسخ الاحتياطي",
                "status": (
                    "unknown"
                    if not backup_configured or not backup_status
                    else "healthy"
                    if backup_status == "success"
                    else "degraded"
                ),
                "detail": backup_status or (
                    "غير مهيأ" if not backup_configured else "لم تُسجل محاولة"
                ),
            },
            {
                "id": "github",
                "name": "المستودع والفحوص الآلية",
                "status": github["status"],
                "detail": github.get("error") or github["repository"],
            },
        ]
        return JSONResponse({
            "ok": True,
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "services": services,
            "github": github,
        })

    # ------------------------------------------------------------
    # 6) إحصاءات + نسخ القاعدة
    # ------------------------------------------------------------
    @app.get("/api/admin/stats")
    async def stats_api(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        db = db_of()
        since = (datetime.now(timezone.utc) - timedelta(days=6)).date().isoformat()
        daily = await db.count_daily(since)
        week_sum = sum(int(d.get("n") or 0) for d in daily)
        return JSONResponse({
            "ok": True,
            "daily7": daily,
            "week_avg": round(week_sum / 7, 1),
            "by_type": await db.count_by_type(),
            "top_users": await db.top_users(7),
        })

    @app.get("/api/admin/backup/download")
    async def backup_download(request: Request):
        """تنزيل لقطة SQLite من قاعدة البيانات الحالية — مسجلة في التدقيق."""
        refuse = _auth(request)
        if refuse:
            return refuse
        db = db_of()
        import tempfile  # noqa: PLC0415
        from pathlib import Path  # noqa: PLC0415
        from starlette.background import BackgroundTask  # noqa: PLC0415

        fd, snapshot_path = tempfile.mkstemp(prefix="mrw-backup-", suffix=".db")
        os.close(fd)
        p = Path(snapshot_path)
        try:
            await asyncio.to_thread(db.export_sqlite_snapshot, p)
        except Exception:
            logger.exception("تعذر إنشاء لقطة SQLite للتنزيل")
            p.unlink(missing_ok=True)
            return bad("backup_unavailable", 503)
        cleanup = BackgroundTask(lambda: p.unlink(missing_ok=True))
        try:
            await db.audit(
                _admin_actor(request),
                "panel_backup_download",
                db.backend,
                "تنزيل نسخة قاعدة البيانات",
            )
        except Exception:
            p.unlink(missing_ok=True)
            raise
        fname = "reports-{ts}.db".format(
            ts=datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        )
        return FileResponse(
            str(p),
            filename=fname,
            media_type="application/x-sqlite3",
            background=cleanup,
        )

    @app.post("/api/admin/backup/now")
    async def backup_now(request: Request) -> JSONResponse:
        """رفع فوري لقطة متسقة إلى مستودع النسخ الخاص (نفس طبقة طبقة الأمان)."""
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        db = db_of()
        import hub_backup  # noqa: PLC0415

        ok = await hub_backup.upload_database_backup(db)
        await db.audit(_admin_actor(request), "panel_backup_upload", "dataset",
                       "نجاح" if ok else "غير مُهيأ/فشل")
        return JSONResponse({"ok": True, "uploaded": bool(ok)})

    @app.get("/api/admin/export/{fmt}")
    async def export(fmt: str, request: Request):
        refuse = _auth(request)
        if refuse:
            return refuse
        if fmt not in ("xlsx", "txt"):
            return bad("bad_format")
        import asyncio as aio  # noqa: PLC0415
        import exporters  # noqa: PLC0415

        db = db_of()
        rows = await db.all_reports()
        if not rows:
            return bad("empty", 404)
        path = await aio.to_thread(
            exporters.export_excel if fmt == "xlsx" else exporters.export_text, rows
        )
        await db.audit(
            _admin_actor(request), "panel_export", fmt, f"عدد البلاغات: {len(rows)}"
        )
        return FileResponse(str(path), filename=path.name)


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
