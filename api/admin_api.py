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

    @app.post("/api/admin/broadcast")
    async def broadcast(request: Request) -> JSONResponse:
        """Start a rate-limited background broadcast to every registered subscriber."""
        refuse = _auth(request)
        if refuse:
            return refuse
        body = await _json(request)
        text = str(body.get("text") or "").strip()
        if not 3 <= len(text) <= 1000:
            return bad("text_length")
        db = db_of()
        ids, _overflow = await db.all_user_ids()
        if not ids:
            return JSONResponse(
                {"ok": True, "sent": 0, "failed": 0, "total": 0}
            )
        bot, loop = _bot_runtime()
        if bot is None:
            return bad("bot_not_ready", 503)
        actor_id = _admin_actor(request)
        job_id = await db.create_broadcast_job(text, actor_id, len(ids))
        if job_id is None:
            return bad("broadcast_in_progress", 409)

        async def _send_all():
            sent = failed = 0
            payload = texts.BROADCAST_NOTIF.format(text=utils.esc(text))
            try:
                for index, uid in enumerate(ids, 1):
                    if index == 1 or index % 5 == 0:
                        job = await db.get_broadcast_job(job_id)
                        if job and job.get("status") == "cancel_requested":
                            await db.update_broadcast_job(
                                job_id, sent, failed, status="cancelled"
                            )
                            await db.audit(
                                actor_id,
                                "broadcast_cancelled",
                                str(job_id),
                                f"توقف بعد نجاح {sent} وتعذر {failed}",
                            )
                            return
                    try:
                        telegram_message = await bot.send_message(
                            chat_id=uid, text=payload, parse_mode=_parse_html()
                        )
                        sent += 1
                        await db.record_broadcast_delivery(
                            job_id,
                            uid,
                            int(telegram_message.message_id),
                            datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        )
                    except Exception:
                        failed += 1
                        logger.debug("Broadcast delivery failed for one recipient")
                    if index % 20 == 0:
                        await db.update_broadcast_job(job_id, sent, failed)
                    await asyncio.sleep(0.08)
                final_status = await db.finish_broadcast_job(job_id, sent, failed)
                if final_status == "cancelled":
                    await db.audit(
                        actor_id,
                        "broadcast_cancelled",
                        str(job_id),
                        f"توقف بعد نجاح {sent} وتعذر {failed}",
                    )
                    return
                await db.audit(
                    actor_id,
                    "broadcast",
                    "الجميع",
                    f"أُرسل لـ {sent} وفشل {failed}",
                )
            except Exception:
                logger.exception("Broadcast job %s failed", job_id)
                await db.update_broadcast_job(job_id, sent, failed, status="failed")
                await db.audit(
                    actor_id,
                    "broadcast_failed",
                    str(job_id),
                    f"توقف بعد نجاح {sent} وتعذر {failed}",
                )

        def _schedule():
            asyncio.create_task(_send_all())

        try:
            loop.call_soon_threadsafe(_schedule)
        except RuntimeError:
            await db.update_broadcast_job(job_id, 0, 0, status="failed")
            await db.audit(
                actor_id, "broadcast_failed", str(job_id), "حلقة البوت غير متاحة"
            )
            return bad("bot_not_ready", 503)
        await db.audit(
            actor_id, "broadcast_started", str(job_id), f"المستهدفون: {len(ids)}"
        )
        return JSONResponse({
            "ok": True,
            "job_id": job_id,
            "status": "running",
            "sent": 0,
            "failed": 0,
            "total": len(ids),
        })

    @app.get("/api/admin/publications/summary")
    async def publications_summary(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        ids, overflow = await db_of().publication_subscriber_ids(cap=100_000)
        return JSONResponse({
            "ok": True,
            "recipients": len(ids),
            "count_capped": overflow,
            "photo_max_bytes": 10 * 1024 * 1024,
            "other_media_max_bytes": 20 * 1024 * 1024,
        })

    @app.post("/api/admin/publications/preview")
    async def publication_preview(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        try:
            form = await request.form()
        except Exception:
            logger.exception("تعذر قراءة ملف معاينة المنشور")
            return bad("multipart_unavailable", 503)
        caption = str(form.get("caption") or "").strip()
        media_type = str(form.get("media_type") or "").strip()
        category = str(form.get("category") or "public").strip()
        upload = form.get("media")
        if len(caption) > 900:
            return bad("caption_too_long")
        if category not in PUBLICATION_CATEGORY_LABELS:
            return bad("bad_publication_category")
        if media_type not in {"", "photo", "video", "document"}:
            return bad("bad_media_type")
        if media_type and not upload:
            return bad("media_required")
        if not media_type and len(caption) < 3:
            return bad("publication_content_required")
        media_data = b""
        filename = "attachment"
        if media_type:
            if not hasattr(upload, "read"):
                return bad("bad_media")
            filename = os.path.basename(str(getattr(upload, "filename", "") or "attachment"))
            filename = filename.replace("\x00", "")[:180] or "attachment"
            limit = 10 * 1024 * 1024 if media_type == "photo" else 20 * 1024 * 1024
            try:
                media_data = await upload.read(limit + 1)
            finally:
                await upload.close()
            if len(media_data) > limit:
                return bad("media_too_large", 413)
            if not media_data:
                return bad("empty_media")
            if media_type == "photo" and _image_media_type(media_data, filename) not in {
                "image/jpeg",
                "image/png",
            }:
                return bad("unsupported_photo_format")
            if media_type == "video" and (
                len(media_data) < 12 or media_data[4:8] != b"ftyp"
            ):
                return bad("invalid_video_format")

        from app import config  # noqa: PLC0415
        from telegram import InputFile  # noqa: PLC0415
        from telegram.error import BadRequest, Forbidden, NetworkError  # noqa: PLC0415

        admin_id = _admin_actor(request)
        if admin_id not in config.ADMIN_IDS:
            admin_id = config.ADMIN_IDS[0] if config.ADMIN_IDS else 0
        if admin_id <= 0:
            return bad("preview_admin_not_configured", 503)
        bot, _loop = _bot_runtime()
        if bot is None:
            return bad("bot_not_ready", 503)

        async def _send_preview():
            try:
                text = _publication_delivery_text(caption, media_type, category)
                if not media_type:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=text,
                        parse_mode=_parse_html(),
                    )
                    return {"file_id": None}
                media_file = InputFile(media_data, filename=filename)
                if media_type == "photo":
                    message = await bot.send_photo(
                        chat_id=admin_id, photo=media_file, caption=text
                    )
                    media = message.photo[-1] if message.photo else None
                elif media_type == "video":
                    message = await bot.send_video(
                        chat_id=admin_id, video=media_file, caption=text
                    )
                    media = message.video
                else:
                    message = await bot.send_document(
                        chat_id=admin_id, document=media_file, caption=text
                    )
                    media = message.document
                if media is None or not getattr(media, "file_id", None):
                    return {"error_code": "preview_media_id_missing"}
                return {"file_id": media.file_id}
            except Forbidden:
                return {"error_code": "preview_admin_unreachable"}
            except BadRequest:
                return {"error_code": "preview_content_rejected"}
            except NetworkError:
                return {"error_code": "telegram_network_error"}
            except Exception:
                logger.exception("فشل إرسال معاينة المنشور")
                return {"error_code": "preview_send_failed"}

        result = await _on_bot_loop_value(_send_preview)
        if not isinstance(result, dict):
            return bad("bot_not_ready", 503)
        if result.get("error_code"):
            return bad(str(result["error_code"]), 502)
        await db_of().audit(
            _admin_actor(request),
            "publication_preview",
            str(media_type or "text"),
            "أُرسلت المعاينة إلى مشرف",
        )
        return JSONResponse({
            "ok": True,
            "preview_sent": True,
            "category": category,
            "media_type": media_type or None,
            "file_id": result.get("file_id"),
            "filename": filename if media_type else None,
        })

    @app.post("/api/admin/publications/send")
    async def publication_send(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        body = await _json(request)
        caption = str(body.get("caption") or "").strip()
        media_type = str(body.get("media_type") or "").strip()
        file_id = str(body.get("file_id") or "").strip()
        category = str(body.get("category") or "public").strip()
        if len(caption) > 900:
            return bad("caption_too_long")
        if media_type not in {"", "photo", "video", "document"}:
            return bad("bad_media_type")
        if category not in PUBLICATION_CATEGORY_LABELS:
            return bad("bad_publication_category")
        if media_type and not file_id:
            return bad("media_preview_required")
        if not media_type and len(caption) < 3:
            return bad("publication_content_required")
        db = db_of()
        ids, overflow = await db.publication_subscriber_ids(cap=100_000)
        if overflow:
            return bad("recipient_limit_exceeded", 413)
        if not ids:
            return JSONResponse({"ok": True, "sent": 0, "failed": 0, "total": 0})
        bot, loop = _bot_runtime()
        if bot is None:
            return bad("bot_not_ready", 503)
        actor_id = _admin_actor(request)
        job_id = await db.create_broadcast_job(
            f"[{category}] " + (caption or f"منشور وسائط: {media_type}"),
            actor_id,
            len(ids),
            media_type or "text",
        )
        if job_id is None:
            return bad("broadcast_in_progress", 409)

        async def _send_to_user(user_id: int) -> None:
            text = _publication_delivery_text(caption, media_type, category)
            if not media_type:
                return await bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode=_parse_html(),
                )
            send_method = {
                "photo": bot.send_photo,
                "video": bot.send_video,
                "document": bot.send_document,
            }[media_type]
            media_field = {
                "photo": "photo",
                "video": "video",
                "document": "document",
            }[media_type]
            return await send_method(
                chat_id=user_id,
                caption=text,
                **{media_field: file_id},
            )

        async def _send_all():
            sent = failed = 0
            try:
                for index, uid in enumerate(ids, 1):
                    if index == 1 or index % 5 == 0:
                        job = await db.get_broadcast_job(job_id)
                        if job and job.get("status") == "cancel_requested":
                            await db.update_broadcast_job(
                                job_id, sent, failed, status="cancelled"
                            )
                            await db.audit(
                                actor_id,
                                "publication_cancelled",
                                str(job_id),
                                f"نجح {sent} وتعذر {failed}",
                            )
                            return
                    try:
                        telegram_message = await _send_to_user(uid)
                        sent += 1
                        await db.record_broadcast_delivery(
                            job_id,
                            uid,
                            int(telegram_message.message_id),
                            datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        )
                    except Exception:
                        failed += 1
                        logger.debug("Publication delivery failed for one recipient")
                    if index % 10 == 0:
                        await db.update_broadcast_job(job_id, sent, failed)
                    await asyncio.sleep(0.1)
                final_status = await db.finish_broadcast_job(job_id, sent, failed)
                if final_status == "cancelled":
                    await db.audit(
                        actor_id,
                        "publication_cancelled",
                        str(job_id),
                        f"نجح {sent} وتعذر {failed}",
                    )
                    return
                await db.audit(
                    actor_id,
                    "publication_sent",
                    str(job_id),
                    f"category={category}; type={media_type or 'text'}; "
                    f"sent={sent}; failed={failed}",
                )
            except Exception:
                logger.exception("Publication job %s failed", job_id)
                await db.update_broadcast_job(
                    job_id, sent, failed, status="failed"
                )
                await db.audit(
                    actor_id,
                    "publication_failed",
                    str(job_id),
                    f"sent={sent}; failed={failed}",
                )

        def _schedule():
            asyncio.create_task(_send_all())

        try:
            loop.call_soon_threadsafe(_schedule)
        except RuntimeError:
            await db.update_broadcast_job(job_id, 0, 0, status="failed")
            return bad("bot_not_ready", 503)
        await db.audit(
            actor_id,
            "publication_started",
            str(job_id),
            f"type={media_type or 'text'}; recipients={len(ids)}",
        )
        return JSONResponse({
            "ok": True,
            "job_id": job_id,
            "status": "running",
            "sent": 0,
            "failed": 0,
            "total": len(ids),
            "media_type": media_type or None,
        })

    @app.get("/api/admin/broadcast")
    async def broadcast_history(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        try:
            limit = min(max(int(request.query_params.get("limit", "30")), 1), 100)
        except ValueError:
            return bad("bad_query")
        items = await db_of().list_broadcast_jobs(limit)
        return JSONResponse({"ok": True, "items": items})

    @app.post("/api/admin/broadcast/{job_id}/delete")
    async def broadcast_delete(job_id: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        db = db_of()
        actor_id = _admin_actor(request)
        prepared = await db.prepare_broadcast_delete(job_id, actor_id)
        if prepared.get("error"):
            return bad(str(prepared["error"]), 409 if prepared["error"] != "not_found" else 404)
        if not prepared.get("total"):
            return JSONResponse({
                "ok": True,
                "status": "completed",
                "deleted": prepared.get("deleted", 0),
                "expired": prepared.get("expired", 0),
            })
        bot, loop = _bot_runtime()
        if bot is None or loop is None:
            await db.update_broadcast_delete_job(
                job_id, 0, 0, int(prepared.get("expired") or 0), "failed"
            )
            return bad("bot_not_ready", 503)
        deliveries = prepared["deliveries"]

        async def _delete_all():
            deleted = failed = 0
            expired = int(prepared.get("expired") or 0)
            try:
                for index, delivery in enumerate(deliveries, 1):
                    try:
                        await bot.delete_message(
                            chat_id=delivery["user_id"],
                            message_id=delivery["message_id"],
                        )
                        deleted += 1
                        status, error = "deleted", None
                    except Exception:
                        failed += 1
                        status, error = "failed", "telegram_delete_failed"
                    await db.update_broadcast_delivery_delete(
                        job_id, delivery["user_id"], status, error
                    )
                    if status == "deleted":
                        await db.update_message_by_telegram_id(
                            delivery["user_id"],
                            delivery["message_id"],
                            None,
                            deleted=True,
                        )
                    if index % 20 == 0:
                        await db.update_broadcast_delete_job(
                            job_id, deleted, failed, expired
                        )
                    await asyncio.sleep(0.05)
                await db.update_broadcast_delete_job(
                    job_id, deleted, failed, expired, "completed"
                )
                await db.audit(
                    actor_id, "broadcast_deleted", str(job_id),
                    f"deleted={deleted}; failed={failed}; expired={expired}",
                )
            except Exception:
                logger.exception("Broadcast deletion job %s failed", job_id)
                await db.update_broadcast_delete_job(
                    job_id, deleted, failed, expired, "failed"
                )

        try:
            loop.call_soon_threadsafe(lambda: asyncio.create_task(_delete_all()))
        except RuntimeError:
            await db.update_broadcast_delete_job(
                job_id, 0, 0, int(prepared.get("expired") or 0), "failed"
            )
            return bad("bot_not_ready", 503)
        await db.audit(
            actor_id, "broadcast_delete_started", str(job_id),
            f"eligible={prepared['total']}; expired={prepared.get('expired', 0)}",
        )
        return JSONResponse({
            "ok": True,
            "status": "running",
            "job_id": job_id,
            "total": prepared["total"],
            "expired": prepared.get("expired", 0),
        })

    @app.post("/api/admin/broadcast/{job_id}/edit")
    async def broadcast_edit(job_id: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        body = await _json(request)
        content = str(body.get("text") or "").strip()
        db = db_of()
        job = await db.get_broadcast_job(job_id)
        if not job:
            return bad("not_found", 404)
        limit = 900 if job.get("media_type") not in ("", "text") else 1000
        if not 1 <= len(content) <= limit:
            return bad("text_length")
        actor_id = _admin_actor(request)
        prepared = await db.prepare_broadcast_edit(job_id, actor_id, content)
        if prepared.get("error"):
            return bad(str(prepared["error"]), 409 if prepared["error"] != "not_found" else 404)
        if not prepared.get("total"):
            return JSONResponse({
                "ok": True,
                "status": "completed",
                "edited": 0,
                "expired": prepared.get("expired", 0),
            })
        bot, loop = _bot_runtime()
        if bot is None or loop is None:
            await db.update_broadcast_edit_job(
                job_id, 0, 0, int(prepared.get("expired") or 0), "failed"
            )
            return bad("bot_not_ready", 503)
        deliveries = prepared["deliveries"]
        media_type = str(job.get("media_type") or "")

        async def _edit_all():
            edited = failed = 0
            expired = int(prepared.get("expired") or 0)
            try:
                for index, delivery in enumerate(deliveries, 1):
                    try:
                        if media_type in ("", "text"):
                            edited_text = texts.BROADCAST_NOTIF.format(
                                text=utils.esc(content)
                            )
                            await bot.edit_message_text(
                                chat_id=delivery["user_id"],
                                message_id=delivery["message_id"],
                                text=edited_text,
                                parse_mode=_parse_html(),
                            )
                        else:
                            edited_text = (
                                f"📨 منشور من الجهة المختصة\n\n{content}"
                            )
                            await bot.edit_message_caption(
                                chat_id=delivery["user_id"],
                                message_id=delivery["message_id"],
                                caption=edited_text,
                            )
                        edited += 1
                        status, error = "edited", None
                        await db.update_message_by_telegram_id(
                            delivery["user_id"],
                            delivery["message_id"],
                            edited_text,
                            deleted=False,
                        )
                    except Exception:
                        failed += 1
                        status, error = "failed", "telegram_edit_failed"
                    await db.update_broadcast_delivery_edit(
                        job_id, delivery["user_id"], status, error
                    )
                    if index % 20 == 0:
                        await db.update_broadcast_edit_job(
                            job_id, edited, failed, expired
                        )
                    await asyncio.sleep(0.05)
                await db.update_broadcast_edit_job(
                    job_id, edited, failed, expired, "completed"
                )
                await db.audit(
                    actor_id, "broadcast_edited", str(job_id),
                    f"edited={edited}; failed={failed}; expired={expired}",
                )
            except Exception:
                logger.exception("Broadcast edit job %s failed", job_id)
                await db.update_broadcast_edit_job(
                    job_id, edited, failed, expired, "failed"
                )

        try:
            loop.call_soon_threadsafe(lambda: asyncio.create_task(_edit_all()))
        except RuntimeError:
            await db.update_broadcast_edit_job(
                job_id, 0, 0, int(prepared.get("expired") or 0), "failed"
            )
            return bad("bot_not_ready", 503)
        await db.audit(
            actor_id, "broadcast_edit_started", str(job_id),
            f"eligible={prepared['total']}; expired={prepared.get('expired', 0)}",
        )
        return JSONResponse({
            "ok": True,
            "status": "running",
            "job_id": job_id,
            "total": prepared["total"],
            "expired": prepared.get("expired", 0),
        })

    @app.get("/api/admin/broadcast/{job_id}")
    async def broadcast_status(job_id: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        job = await db_of().get_broadcast_job(job_id)
        if not job:
            return bad("not_found", 404)
        return JSONResponse({"ok": True, **job})

    @app.post("/api/admin/broadcast/{job_id}/cancel")
    async def broadcast_cancel(job_id: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        db = db_of()
        if not await db.request_broadcast_cancel(job_id):
            return bad("broadcast_not_active", 409)
        await db.audit(
            _admin_actor(request), "broadcast_cancel_requested", str(job_id), ""
        )
        return JSONResponse({"ok": True, "status": "cancel_requested"})

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
    def _bot_menu_actions() -> list[dict[str, str]]:
        from bot import keyboards  # noqa: PLC0415

        return [
            {"id": action, "label": label}
            for action, label in keyboards.USER_MENU_ACTIONS.items()
        ]

    def _bot_menu_limits() -> dict[str, int]:
        from bot import keyboards  # noqa: PLC0415

        return {
            "max_rows": keyboards.MAX_USER_MENU_ROWS,
            "max_row_buttons": keyboards.MAX_USER_MENU_ROW_BUTTONS,
            "max_buttons": keyboards.MAX_USER_MENU_BUTTONS,
            "max_label": keyboards.MAX_USER_MENU_LABEL,
            "max_url": keyboards.MAX_USER_MENU_URL,
            "max_copy": keyboards.MAX_USER_MENU_COPY,
        }

    def _bot_menu_options() -> dict[str, list[str]]:
        from bot import control, keyboards  # noqa: PLC0415

        return {
            "styles": list(control.STYLE_VALUES),
            "types": list(keyboards.MENU_BUTTON_TYPES),
            "visibility": list(keyboards.MENU_VISIBILITY),
        }

    @app.get("/api/admin/bot-buttons")
    async def bot_buttons_get(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        from bot import keyboards  # noqa: PLC0415

        db = db_of()
        raw = await db.get_state(keyboards.USER_MENU_STATE_KEY)
        layout = keyboards.normalize_user_menu_layout(raw)
        return JSONResponse({
            "ok": True,
            "items": layout or keyboards.default_user_menu_layout(),
            "defaults": keyboards.default_user_menu_layout(),
            "customized": layout is not None,
            "actions": _bot_menu_actions(),
            "limits": _bot_menu_limits(),
            "options": _bot_menu_options(),
        })

    @app.put("/api/admin/bot-buttons")
    async def bot_buttons_put(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        from bot import keyboards  # noqa: PLC0415

        body = await _json(request)
        layout = keyboards.normalize_user_menu_layout(body.get("items"))
        if layout is None:
            return bad("bad_bot_menu_layout")
        db = db_of()
        await db.set_state(
            keyboards.USER_MENU_STATE_KEY,
            json.dumps(layout, ensure_ascii=False),
        )
        keyboards.set_user_menu_layout(layout)
        await db.audit(
            _admin_actor(request),
            "bot_menu_changed",
            "أزرار قائمة المستخدم",
            f"{len(layout)} صفوف — {sum(len(row) for row in layout)} أزرار",
        )
        return JSONResponse({
            "ok": True,
            "items": layout,
            "actions": _bot_menu_actions(),
            "limits": _bot_menu_limits(),
            "customized": True,
        })

    @app.delete("/api/admin/bot-buttons")
    async def bot_buttons_reset(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        from bot import keyboards  # noqa: PLC0415

        db = db_of()
        await db.set_state(keyboards.USER_MENU_STATE_KEY, None)
        keyboards.set_user_menu_layout(None)
        await db.audit(
            _admin_actor(request),
            "bot_menu_reset",
            "أزرار قائمة المستخدم",
            "العودة إلى التخطيط الافتراضي",
        )
        return JSONResponse({
            "ok": True,
            "items": keyboards.default_user_menu_layout(),
            "actions": _bot_menu_actions(),
            "limits": _bot_menu_limits(),
            "customized": False,
        })

    # ------------------------------------------------------------------
    # مركز التحكم الكامل: لوحات الرد، الأوامر، زر القائمة، الإعدادات
    # ------------------------------------------------------------------
    @app.get("/api/admin/bot-reply-keyboards")
    async def bot_reply_keyboards_get(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        db = db_of()
        raw = await db.get_state(control.REPLY_STATE_KEY)
        items = control.normalize_reply_keyboards(raw)
        bot, _ = _bot_runtime()
        return JSONResponse({
            "ok": True,
            "items": items or control.default_reply_keyboards(),
            "defaults": control.default_reply_keyboards(),
            "ids": control.reply_keyboard_ids(),
            "customized": items is not None,
            "mini_app_url": control.mini_app_url(),
            "bot_ready": bot is not None,
            "types": list(control.REPLY_BUTTON_TYPES),
            "styles": list(control.STYLE_VALUES),
        })

    @app.put("/api/admin/bot-reply-keyboards")
    async def bot_reply_keyboards_put(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        body = await _json(request)
        items = control.normalize_reply_keyboards(body.get("items"))
        if items is None:
            return bad("bad_bot_reply_keyboards")
        db = db_of()
        await db.set_state(
            control.REPLY_STATE_KEY, json.dumps(items, ensure_ascii=False)
        )
        control.set_reply_keyboards(items)
        await db.audit(
            _admin_actor(request),
            "bot_reply_keyboards_changed",
            "لوحات رد البوت",
            f"{len(items)} لوحات",
        )
        return JSONResponse({
            "ok": True,
            "items": items,
            "ids": control.reply_keyboard_ids(),
            "customized": True,
            "types": list(control.REPLY_BUTTON_TYPES),
            "styles": list(control.STYLE_VALUES),
        })

    @app.delete("/api/admin/bot-reply-keyboards")
    async def bot_reply_keyboards_reset(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        db = db_of()
        await db.set_state(control.REPLY_STATE_KEY, None)
        control.set_reply_keyboards(None)
        await db.audit(
            _admin_actor(request),
            "bot_reply_keyboards_reset",
            "لوحات رد البوت",
            "العودة إلى الافتراضي",
        )
        return JSONResponse({
            "ok": True,
            "items": control.default_reply_keyboards(),
            "ids": control.reply_keyboard_ids(),
            "customized": False,
            "types": list(control.REPLY_BUTTON_TYPES),
            "styles": list(control.STYLE_VALUES),
        })

    @app.get("/api/admin/bot-commands")
    async def bot_commands_get(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        db = db_of()
        raw = await db.get_state(control.COMMANDS_STATE_KEY)
        cfg = control.normalize_commands(raw)
        bot, _ = _bot_runtime()
        return JSONResponse({
            "ok": True,
            "items": cfg or control.default_commands_config(),
            "defaults": control.default_commands_config(),
            "customized": cfg is not None,
            "scopes": [
                {"id": key, "label": label}
                for key, label in control._SCOPE_LABELS.items()
            ],
            "bot_ready": bot is not None,
            "limits": {
                "max_commands": control.MAX_COMMANDS,
                "max_scopes": control.MAX_SCOPES,
            },
        })

    @app.put("/api/admin/bot-commands")
    async def bot_commands_put(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        body = await _json(request)
        new_cfg = control.normalize_commands(body.get("items", body))
        if new_cfg is None:
            return bad("bad_bot_commands")
        db = db_of()
        old_raw = await db.get_state(control.COMMANDS_STATE_KEY)
        old_cfg = control.normalize_commands(old_raw) or control.default_commands_config()
        await db.set_state(
            control.COMMANDS_STATE_KEY, json.dumps(new_cfg, ensure_ascii=False)
        )
        control.set_commands_config(new_cfg)

        applied: dict[str, Any] = {"ok": False, "errors": ["bot_not_ready"]}
        bot, _ = _bot_runtime()
        if bot is not None:
            removed = control.removed_scopes(old_cfg, new_cfg)

            async def _push() -> dict[str, Any]:
                result = await control.apply_commands(bot, new_cfg)
                for item in removed:
                    await control.delete_commands_scope(bot, item)
                return result

            result = await _on_bot_loop_value(_push)
            if isinstance(result, dict):
                applied = result
        await db.audit(
            _admin_actor(request),
            "bot_commands_changed",
            "أوامر البوت",
            f"{len(new_cfg['scopes'])} نطاق — applied={applied.get('ok')}",
        )
        return JSONResponse({
            "ok": True,
            "items": new_cfg,
            "customized": True,
            "applied": applied,
            "bot_ready": bot is not None,
        })

    @app.get("/api/admin/bot-menu-button")
    async def bot_menu_button_get(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        db = db_of()
        raw = await db.get_state(control.MENU_BUTTON_STATE_KEY)
        cfg = control.normalize_menu_button(raw)
        bot, _ = _bot_runtime()
        return JSONResponse({
            "ok": True,
            "items": cfg or control.default_menu_button(),
            "defaults": control.default_menu_button(),
            "customized": cfg is not None,
            "bot_ready": bot is not None,
            "mini_app_url": control.mini_app_url(),
        })

    @app.put("/api/admin/bot-menu-button")
    async def bot_menu_button_put(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        body = await _json(request)
        cfg = control.normalize_menu_button(body.get("items", body))
        if cfg is None:
            return bad("bad_bot_menu_button")
        db = db_of()
        await db.set_state(
            control.MENU_BUTTON_STATE_KEY, json.dumps(cfg, ensure_ascii=False)
        )
        control.set_menu_button_config(cfg)
        applied = False
        bot, _ = _bot_runtime()
        if bot is not None:
            result = await _on_bot_loop_value(
                lambda: control.apply_menu_button(bot, cfg)
            )
            applied = bot is not None
        await db.audit(
            _admin_actor(request),
            "bot_menu_button_changed",
            "زر قائمة البوت",
            cfg.get("type", ""),
        )
        return JSONResponse({
            "ok": True,
            "items": cfg,
            "customized": True,
            "applied": applied,
            "bot_ready": bot is not None,
        })

    @app.delete("/api/admin/bot-menu-button")
    async def bot_menu_button_reset(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        db = db_of()
        await db.set_state(control.MENU_BUTTON_STATE_KEY, None)
        cfg = control.set_menu_button_config(None)
        bot, _ = _bot_runtime()
        if bot is not None:
            await _on_bot_loop_value(lambda: control.apply_menu_button(bot, cfg))
        await db.audit(
            _admin_actor(request),
            "bot_menu_button_reset",
            "زر قائمة البوت",
            "العودة إلى الافتراضي",
        )
        return JSONResponse({
            "ok": True,
            "items": cfg,
            "customized": False,
            "applied": bot is not None,
            "bot_ready": bot is not None,
        })

    @app.get("/api/admin/bot-settings")
    async def bot_settings_get(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        db = db_of()
        raw = await db.get_state(control.SETTINGS_STATE_KEY)
        cfg = control.normalize_settings(raw)
        values = cfg or control.default_settings()
        bot, _ = _bot_runtime()
        info: dict[str, Any] = {
            "username": "",
            "supports_inline_queries": False,
            "can_join_groups": False,
            "can_read_all_group_messages": False,
        }
        if bot is not None:
            async def _me():
                me = await bot.get_me()
                return {
                    "username": me.username or "",
                    "supports_inline_queries": bool(me.supports_inline_queries),
                    "can_join_groups": bool(me.can_join_groups),
                    "can_read_all_group_messages": bool(
                        me.can_read_all_group_messages
                    ),
                }

            remote = await _on_bot_loop_value(_me)
            if isinstance(remote, dict):
                info = remote
        return JSONResponse({
            "ok": True,
            "items": values,
            "defaults": control.default_settings(),
            "customized": cfg is not None,
            "bot_ready": bot is not None,
            "bot": info,
            "rights_keys": list(control._RIGHTS_KEYS),
        })

    @app.put("/api/admin/bot-settings")
    async def bot_settings_put(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        body = await _json(request)
        cfg = control.normalize_settings(body.get("items", body))
        if cfg is None:
            return bad("bad_bot_settings")
        db = db_of()
        await db.set_state(
            control.SETTINGS_STATE_KEY, json.dumps(cfg, ensure_ascii=False)
        )
        control.set_settings(cfg)
        applied = False
        bot, _ = _bot_runtime()
        if bot is not None:
            result = await _on_bot_loop_value(
                lambda: control.apply_default_admin_rights(bot, cfg)
            )
            applied = bot is not None
        await db.audit(
            _admin_actor(request),
            "bot_settings_changed",
            "إعدادات البوت",
            f"mini_app={'yes' if cfg['mini_app_url'] else 'no'}; "
            f"rights_customized={cfg['default_admin_rights_customized']}",
        )
        return JSONResponse({
            "ok": True,
            "items": cfg,
            "customized": True,
            "applied": applied,
            "bot_ready": bot is not None,
        })

    @app.get("/api/admin/bot-analytics")
    async def bot_analytics_get(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        from bot import keyboards  # noqa: PLC0415

        rows = await db_of().button_stats()
        labels = dict(keyboards.USER_MENU_ACTIONS)
        for action, label in {
            "menu": "القائمة الرئيسية",
            "home": "العودة للقائمة",
            "category": "اختيار التصنيف",
            "r": "فتح بلاغ",
            "back": "رجوع",
            "verify-consent": "الموافقة على التوثيق",
            "verify-supplement": "استكمال التوثيق",
            "report": "تقديم بلاغ",
            "status": "متابعة بلاغاتي",
            "about": "دليل الاستخدام",
            "contact": "التواصل مع الإدارة",
            "privacy": "سياسة الخصوصية",
            "verify": "التسجيل كمراسل",
            "verify-status": "حالة التوثيق",
            "cancel": "إلغاء",
            "confirm": "تأكيد الإرسال",
        }.items():
            labels.setdefault(action, label)
        items = [
            {
                "action": row["action"],
                "label": labels.get(row["action"], row["action"]),
                "presses": row["presses"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
        return JSONResponse({
            "ok": True,
            "items": items,
            "total": sum(item["presses"] for item in items),
            "tracked_actions": len(items),
        })

    @app.delete("/api/admin/bot-analytics")
    async def bot_analytics_reset(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        await db_of().reset_button_stats()
        await db_of().audit(
            _admin_actor(request),
            "bot_analytics_reset",
            "تحليلات أزرار البوت",
            "تصفير العدّادات",
        )
        return JSONResponse({"ok": True, "items": [], "total": 0})

    @app.get("/api/admin/bot-links")
    async def bot_links_get(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        payload = control.sanitize_payload(request.query_params.get("payload", ""))
        username = ""
        bot, _ = _bot_runtime()
        if bot is not None:
            async def _me():
                me = await bot.get_me()
                return me.username or ""

            remote = await _on_bot_loop_value(_me)
            if isinstance(remote, str):
                username = remote
        return JSONResponse({
            "ok": True,
            "payload": payload,
            "username": username,
            "links": control.deep_links(username, payload),
            "mini_app_url": control.mini_app_url(),
        })

    @app.post("/api/admin/bot-apply")
    async def bot_apply_all(request: Request) -> JSONResponse:
        """إعادة دفع كل إعدادات البوت إلى تيليجرام (بعد إعادة تشغيل مثلًا)."""
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        from bot import control  # noqa: PLC0415

        bot, _ = _bot_runtime()
        if bot is None:
            return bad("bot_not_ready", 503)
        cfg_commands = control.current_commands_config()
        cfg_menu = control.current_menu_button_config()
        cfg_settings = control.current_settings()

        async def _push() -> dict[str, Any]:
            commands = await control.apply_commands(bot, cfg_commands)
            menu_error = None
            try:
                await control.apply_menu_button(bot, cfg_menu)
            except Exception as exc:  # noqa: BLE001
                menu_error = str(exc)
            rights_error = None
            try:
                await control.apply_default_admin_rights(bot, cfg_settings)
            except Exception as exc:  # noqa: BLE001
                rights_error = str(exc)
            return {
                "ok": commands.get("ok") and not menu_error and not rights_error,
                "command_errors": commands.get("errors", []),
                "menu_error": menu_error,
                "rights_error": rights_error,
            }

        result = await _on_bot_loop_value(_push)
        await db_of().audit(
            _admin_actor(request),
            "bot_apply_all",
            "إعادة تطبيق إعدادات البوت",
            str(result),
        )
        return JSONResponse({"ok": True, "result": result or {"ok": False}})

    @app.get("/api/admin/settings")
    async def runtime_settings_get(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        from app import config  # noqa: PLC0415

        saved = await db_of().get_runtime_settings()
        values = {
            "MAX_DESC_LEN": config.MAX_DESC_LEN,
            "MAX_PHOTO_MB": config.MAX_PHOTO_MB,
            "MAX_REPORTS_PER_DAY": config.MAX_REPORTS_PER_DAY,
            "MAX_REPORTS_PER_MINUTE": config.MAX_REPORTS_PER_MINUTE,
            "MAX_REPORTS_PER_HOUR": config.MAX_REPORTS_PER_HOUR,
            "MAX_REPORTS_PER_HOUR_GLOBAL": config.MAX_REPORTS_PER_HOUR_GLOBAL,
            "MAX_REPORTS_PER_DAY_GLOBAL": config.MAX_REPORTS_PER_DAY_GLOBAL,
            "FREEZE_AFTER_STRIKES": config.FREEZE_AFTER_STRIKES,
            "FREEZE_MINUTES_FIRST": config.FREEZE_MINUTES_FIRST,
            "FREEZE_MINUTES_SECOND": config.FREEZE_MINUTES_SECOND,
            "FREEZE_MINUTES_REPEAT": config.FREEZE_MINUTES_REPEAT,
        }
        for key in values:
            candidate = saved.get(key)
            minimum, maximum = config.RUNTIME_SETTING_LIMITS[key]
            if (
                isinstance(candidate, int)
                and not isinstance(candidate, bool)
                and minimum <= candidate <= maximum
            ):
                values[key] = candidate
        import guards  # noqa: PLC0415
        db = db_of()

        return JSONResponse({
            "ok": True,
            "items": {
                "max_description_length": values["MAX_DESC_LEN"],
                "max_photo_mb": values["MAX_PHOTO_MB"],
                "max_reports_per_day": values["MAX_REPORTS_PER_DAY"],
                "max_reports_per_minute": values["MAX_REPORTS_PER_MINUTE"],
                "max_reports_per_hour": values["MAX_REPORTS_PER_HOUR"],
                "max_reports_per_hour_global": values["MAX_REPORTS_PER_HOUR_GLOBAL"],
                "max_reports_per_day_global": values["MAX_REPORTS_PER_DAY_GLOBAL"],
                "freeze_after_strikes": values["FREEZE_AFTER_STRIKES"],
                "freeze_minutes_first": values["FREEZE_MINUTES_FIRST"],
                "freeze_minutes_second": values["FREEZE_MINUTES_SECOND"],
                "freeze_minutes_repeat": values["FREEZE_MINUTES_REPEAT"],
                "auto_freeze_paused_until": await guards.auto_freeze_pause_until(db),
                "user_limits_paused_until": await guards.user_limits_pause_until(db),
                "block_messages": await guards.get_block_messages(db),
            },
        })

    @app.get("/api/admin/bot-profile")
    async def bot_profile_get(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        db = db_of()
        raw = await db.get_state("bot_profile")
        try:
            saved = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            saved = {}
        if not isinstance(saved, dict):
            saved = {}
        values = {
            "name": str(saved.get("name") or ""),
            "short_description": str(saved.get("short_description") or ""),
            "description": str(saved.get("description") or ""),
            "welcome_text": str(
                saved.get("welcome_text") or texts.BOT_PROFILE_DEFAULT_WELCOME
            ),
            "privacy_policy_url": str(saved.get("privacy_policy_url") or ""),
            "verification_consent_text": str(
                saved.get("verification_consent_text") or ""
            ),
            "photo_updated_at": str(saved.get("photo_updated_at") or ""),
            "has_welcome_photo": await db.has_welcome_photo(),
        }
        bot, _ = _bot_runtime()
        if bot is not None:
            async def _load_remote_profile():
                remote = {}
                me = await bot.get_me()
                remote["username"] = me.username or ""
                for key, method_name, attribute in (
                    ("name", "get_my_name", "name"),
                    ("short_description", "get_my_short_description", "short_description"),
                    ("description", "get_my_description", "description"),
                ):
                    method = getattr(bot, method_name, None)
                    if method is not None:
                        result = await method()
                        remote[key] = getattr(result, attribute, "") or ""
                return remote

            remote = await _on_bot_loop_value(_load_remote_profile)
            if isinstance(remote, dict):
                for key in ("name", "short_description", "description"):
                    if key in remote:
                        values[key] = remote[key]
                values["username"] = remote.get("username", "")
        return JSONResponse({
            "ok": True,
            "items": values,
            "bot_ready": bot is not None,
            "can_edit": getattr(getattr(request, "state", None), "admin_role", "") == "owner",
        })

    @app.get("/api/admin/bot-profile/blocked-words")
    async def blocked_words_get(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        raw = await db_of().get_state("support_blocklist")
        try:
            saved = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            logger.exception("تعذرت قراءة قائمة الكلمات المحظورة")
            return bad("wordlist_unavailable", 503)
        if not isinstance(saved, dict) or not isinstance(saved.get("words", []), list):
            return bad("wordlist_unavailable", 503)
        words = saved.get("words", [])
        if any(not isinstance(word, str) for word in words):
            return bad("wordlist_unavailable", 503)
        return JSONResponse({
            "ok": True,
            "count": len(words),
            "content": "\n".join(words),
            "updated_at": saved.get("updated_at"),
        })

    @app.post("/api/admin/bot-profile/blocked-words")
    async def blocked_words_put(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        try:
            form = await request.form()
        except Exception:
            return bad("multipart_unavailable", 503)
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return bad("wordlist_file_required")
        filename = str(getattr(upload, "filename", "") or "")
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1][:120]
        if not filename.lower().endswith(".txt"):
            return bad("wordlist_must_be_txt")
        content = await upload.read(word_filter.MAX_WORD_LIST_BYTES + 1)
        try:
            terms = word_filter.parse_word_list(content)
        except ValueError as exc:
            return bad(str(exc))
        value = {
            "words": terms,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        await db_of().set_state(
            "support_blocklist",
            json.dumps(value, ensure_ascii=False, sort_keys=True),
        )
        await db_of().audit(
            _admin_actor(request),
            "support_blocklist_replaced",
            "قائمة الكلمات المحظورة",
            f"count={len(terms)}; filename={filename}",
        )
        return JSONResponse({"ok": True, "count": len(terms)})

    @app.delete("/api/admin/bot-profile/blocked-words")
    async def blocked_words_delete(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        await db_of().set_state(
            "support_blocklist",
            json.dumps(
                {"words": [], "updated_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                )},
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        await db_of().audit(
            _admin_actor(request),
            "support_blocklist_cleared",
            "قائمة الكلمات المحظورة",
            "تم تفريغ القائمة يدويًا",
        )
        return JSONResponse({"ok": True, "count": 0})

    @app.get("/api/admin/bot-profile/welcome-photo")
    async def welcome_photo_get(request: Request):
        refuse = _auth(request)
        if refuse:
            return refuse
        photo = await db_of().get_welcome_photo()
        if photo is None:
            return bad("welcome_photo_not_found", 404)
        return Response(
            content=photo["data"],
            media_type=photo["content_type"],
            headers={"Cache-Control": "private, no-store"},
        )

    @app.post("/api/admin/bot-profile/welcome-photo")
    async def welcome_photo_put(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        try:
            form = await request.form()
        except Exception:
            logger.exception("تعذر قراءة صورة الترحيب")
            return bad("multipart_unavailable", 503)
        upload = form.get("media")
        if upload is None or not hasattr(upload, "read"):
            return bad("welcome_photo_required")
        photo_bytes = await upload.read(10 * 1024 * 1024 + 1)
        if len(photo_bytes) > 10 * 1024 * 1024:
            return bad("welcome_photo_too_large", 413)
        if photo_bytes.startswith(b"\xff\xd8\xff"):
            content_type = "image/jpeg"
            extension = ".jpg"
        elif photo_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            content_type = "image/png"
            extension = ".png"
        else:
            return bad("welcome_photo_must_be_jpeg_or_png")
        filename = str(getattr(upload, "filename", "") or "")
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1][:120]
        if not filename:
            filename = f"welcome{extension}"

        db = db_of()
        await db.set_welcome_photo(photo_bytes, content_type, filename)
        bot_main = _main_module()
        app = getattr(bot_main, "app", None)
        loop = getattr(app, "bot_data", {}).get("loop") if app else None
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(
                app.bot_data.__setitem__, "welcome_photo", {
                    "data": photo_bytes,
                    "content_type": content_type,
                    "filename": filename,
                }
            )
        await db.audit(
            _admin_actor(request),
            "welcome_photo_changed",
            "هوية البوت",
            f"تحديث صورة الترحيب ({content_type})",
        )
        return JSONResponse({
            "ok": True,
            "has_welcome_photo": True,
            "filename": filename,
        })

    @app.delete("/api/admin/bot-profile/welcome-photo")
    async def welcome_photo_delete(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        db = db_of()
        deleted = await db.delete_welcome_photo()
        bot_main = _main_module()
        app = getattr(bot_main, "app", None)
        loop = getattr(app, "bot_data", {}).get("loop") if app else None
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(
                app.bot_data.__setitem__, "welcome_photo", None
            )
        if deleted:
            await db.audit(
                _admin_actor(request),
                "welcome_photo_removed",
                "هوية البوت",
                "إزالة صورة الترحيب",
            )
        return JSONResponse({"ok": True, "has_welcome_photo": False})

    @app.post("/api/admin/bot-profile")
    async def bot_profile_put(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        bot, _ = _bot_runtime()
        if bot is None:
            return bad("bot_not_ready", 503)
        try:
            form = await request.form()
        except Exception:
            logger.exception("تعذر قراءة إعدادات هوية البوت")
            return bad("multipart_unavailable", 503)

        name = str(form.get("name") or "").strip()
        short_description = str(form.get("short_description") or "").strip()
        description = str(form.get("description") or "").strip()
        welcome_text = str(form.get("welcome_text") or "").strip()
        privacy_policy_url = str(form.get("privacy_policy_url") or "").strip()
        verification_consent_text = str(
            form.get("verification_consent_text") or ""
        ).strip()
        if not name or len(name) > 64:
            return bad("bad_bot_name")
        if len(short_description) > 120:
            return bad("short_description_too_long")
        if len(description) > 512:
            return bad("description_too_long")
        if not welcome_text or len(welcome_text) > 3500:
            return bad("bad_welcome_text")
        if len(verification_consent_text) > 2500:
            return bad("bad_verification_consent_text")
        if privacy_policy_url:
            parsed_policy_url = urllib.parse.urlsplit(privacy_policy_url)
            if (
                len(privacy_policy_url) > 500
                or parsed_policy_url.scheme != "https"
                or not parsed_policy_url.netloc
                or parsed_policy_url.username
                or parsed_policy_url.password
            ):
                return bad("bad_privacy_policy_url")

        upload = form.get("photo") or form.get("media")
        photo_bytes = None
        if upload is not None and hasattr(upload, "read"):
            photo_bytes = await upload.read(10 * 1024 * 1024 + 1)
            if len(photo_bytes) > 10 * 1024 * 1024:
                return bad("profile_photo_too_large", 413)
            if photo_bytes and not photo_bytes.startswith(b"\xff\xd8\xff"):
                return bad("profile_photo_must_be_jpeg")
            if not photo_bytes:
                photo_bytes = None

        db = db_of()
        previous_raw = await db.get_state("bot_profile")
        try:
            previous = json.loads(previous_raw) if previous_raw else {}
        except (TypeError, ValueError):
            previous = {}
        if not isinstance(previous, dict):
            previous = {}

        requested = {
            "name": name,
            "short_description": short_description,
            "description": description,
        }
        applied = await _on_bot_loop_value(
            lambda: _set_telegram_bot_profile(bot, requested, photo_bytes)
        )
        if not isinstance(applied, dict) or not applied.get("ok"):
            error = applied.get("error") if isinstance(applied, dict) else None
            return bad(error or "telegram_profile_update_failed", 502)

        values = {
            "name": name,
            "short_description": short_description,
            "description": description,
            "welcome_text": welcome_text,
            "privacy_policy_url": privacy_policy_url,
            "verification_consent_text": verification_consent_text,
            "photo_updated_at": (
                datetime.now(timezone.utc).isoformat(timespec="seconds")
                if photo_bytes else str(previous.get("photo_updated_at") or "")
            ),
        }
        consent_text_changed = (
            previous.get("verification_consent_text")
            or texts.VERIFICATION_CONSENT_DEFAULT
        ) != verification_consent_text
        await db.set_state(
            "bot_profile", json.dumps(values, ensure_ascii=False, sort_keys=True)
        )
        await db.audit(
            _admin_actor(request),
            "bot_profile_changed",
            "هوية البوت",
            "تحديث بيانات الحساب ورسالة البداية"
            + (" وتغيير الصورة" if photo_bytes else "")
            + (" وتحديث إفصاح توثيق المراسل" if consent_text_changed else ""),
        )
        return JSONResponse({"ok": True, "items": values})

    @app.put("/api/admin/settings")
    async def runtime_settings_put(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        from app import config  # noqa: PLC0415

        body = await _json(request)
        aliases = {
            "max_description_length": "MAX_DESC_LEN",
            "max_photo_mb": "MAX_PHOTO_MB",
            "max_reports_per_day": "MAX_REPORTS_PER_DAY",
            "max_reports_per_minute": "MAX_REPORTS_PER_MINUTE",
            "max_reports_per_hour": "MAX_REPORTS_PER_HOUR",
            "max_reports_per_hour_global": "MAX_REPORTS_PER_HOUR_GLOBAL",
            "max_reports_per_day_global": "MAX_REPORTS_PER_DAY_GLOBAL",
            "freeze_after_strikes": "FREEZE_AFTER_STRIKES",
            "freeze_minutes_first": "FREEZE_MINUTES_FIRST",
            "freeze_minutes_second": "FREEZE_MINUTES_SECOND",
            "freeze_minutes_repeat": "FREEZE_MINUTES_REPEAT",
        }
        if not body or set(body) - set(aliases) - {"block_messages"}:
            return bad("bad_settings")
        db = db_of()
        current = await db.get_runtime_settings()
        clean = {}
        for public_key, config_key in aliases.items():
            raw = body.get(public_key, current.get(config_key, getattr(config, config_key)))
            if isinstance(raw, bool):
                return bad("bad_setting_value")
            try:
                value = int(raw)
            except (TypeError, ValueError):
                return bad("bad_setting_value")
            minimum, maximum = config.RUNTIME_SETTING_LIMITS[config_key]
            if not minimum <= value <= maximum:
                return bad("setting_out_of_range")
            clean[config_key] = value
        if not (
            clean["FREEZE_MINUTES_FIRST"]
            <= clean["FREEZE_MINUTES_SECOND"]
            <= clean["FREEZE_MINUTES_REPEAT"]
        ):
            return bad("freeze_duration_order")
        import guards  # noqa: PLC0415

        try:
            current_messages = guards.clean_block_messages(
                current.get("BLOCK_MESSAGES", texts.BLOCK_MESSAGES)
            )
            clean["BLOCK_MESSAGES"] = guards.clean_block_messages(
                {**current_messages, **body.get("block_messages", {})}
            )
        except (TypeError, ValueError) as exc:
            return bad(str(exc) or "bad_block_messages")

        await db.set_runtime_settings(clean)
        config.apply_runtime_settings(clean)
        await db.audit(
            _admin_actor(request), "runtime_settings_changed", "إعدادات التشغيل",
            ", ".join(
                [f"{key}={value}" for key, value in clean.items()
                 if key != "BLOCK_MESSAGES"]
                + (["تحديث رسائل الحظر"] if "block_messages" in body else [])
            ),
        )

        return JSONResponse({
            "ok": True,
            "items": {
                "max_description_length": clean["MAX_DESC_LEN"],
                "max_photo_mb": clean["MAX_PHOTO_MB"],
                "max_reports_per_day": clean["MAX_REPORTS_PER_DAY"],
                "max_reports_per_minute": clean["MAX_REPORTS_PER_MINUTE"],
                "max_reports_per_hour": clean["MAX_REPORTS_PER_HOUR"],
                "max_reports_per_hour_global": clean["MAX_REPORTS_PER_HOUR_GLOBAL"],
                "max_reports_per_day_global": clean["MAX_REPORTS_PER_DAY_GLOBAL"],
                "freeze_after_strikes": clean["FREEZE_AFTER_STRIKES"],
                "freeze_minutes_first": clean["FREEZE_MINUTES_FIRST"],
                "freeze_minutes_second": clean["FREEZE_MINUTES_SECOND"],
                "freeze_minutes_repeat": clean["FREEZE_MINUTES_REPEAT"],
                "auto_freeze_paused_until": await guards.auto_freeze_pause_until(db),
                "user_limits_paused_until": await guards.user_limits_pause_until(db),
                "block_messages": clean["BLOCK_MESSAGES"],
            },
        })

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
