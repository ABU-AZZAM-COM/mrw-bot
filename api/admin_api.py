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

from bot import texts  # noqa: E402
from services import utils  # noqa: E402
from database import (
    STATUS_APPROVED,
    STATUS_CODES,
    STATUS_NEW,
    STATUS_REJECTED,
    STATUS_REVIEW,
)

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20
MAX_TEXT = 2000
ADMIN_PERMISSIONS = {"read", "review", "users", "operate", "export", "delete"}
ROLE_PERMISSIONS = {
    "viewer": {"read"},
    "reviewer": {"read", "review"},
    "operator": {"read", "review", "users", "operate", "export"},
    "owner": ADMIN_PERMISSIONS,
}
FAR_FUTURE = "9999-12-31T00:00:00"
ADMIN_PASSWORD_ITERATIONS = 600_000
ADMIN_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
ADMIN_MAX_SESSIONS = 8


# ==================================================================
# الحماية بالتوكن
# ==================================================================
def expected_token() -> str:
    """سرّ الواجهة من بيئة المساحة (فارغ = المسارات مغلقة)."""
    return os.getenv("ADMIN_API_TOKEN", "").strip()


def _environment_owner_configured() -> bool:
    if expected_token():
        return True
    raw = os.getenv("ADMIN_API_TOKENS_JSON", "").strip()
    if not raw:
        return False
    try:
        records = json.loads(raw)
    except json.JSONDecodeError:
        return False
    return isinstance(records, list) and any(
        isinstance(entry, dict)
        and entry.get("role") == "owner"
        and isinstance(entry.get("token"), str)
        and bool(entry["token"].strip())
        for entry in records
    )


def token_ok(got: str, expected: str) -> bool:
    """مقارنة زمنية ثابتة أن التوكن المُرسل مطابق للسرّ."""
    import hmac

    got, expected = (got or "").strip(), (expected or "").strip()
    if not got or not expected:
        return False
    return hmac.compare_digest(got, expected)


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _password_credentials(password: str) -> dict[str, Any]:
    salt = secrets.token_bytes(16)
    return {
        "password_salt": salt.hex(),
        "password_hash": hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, ADMIN_PASSWORD_ITERATIONS
        ).hex(),
        "password_iterations": ADMIN_PASSWORD_ITERATIONS,
    }


def _password_matches(account: dict[str, Any], password: str) -> bool:
    salt_hex = account.get("password_salt")
    digest = account.get("password_hash")
    iterations = account.get("password_iterations")
    if (
        not isinstance(salt_hex, str)
        or not isinstance(digest, str)
        or not isinstance(iterations, int)
        or isinstance(iterations, bool)
        or not ADMIN_PASSWORD_ITERATIONS <= iterations <= 1_000_000
    ):
        return False
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    if len(salt) != 16:
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    ).hex()
    import hmac

    return hmac.compare_digest(actual, digest)


def _auth(request):
    """Authenticate the request and enforce the server-side token role."""
    app = getattr(request, "app", None)
    db_provider = getattr(getattr(app, "state", None), "admin_db_provider", None)
    persisted_accounts: list[dict[str, Any]] = []
    bootstrap_disabled = False
    snapshot = getattr(
        getattr(request, "state", None), "admin_auth_snapshot", None
    )
    if snapshot is not None:
        persisted_accounts, bootstrap_disabled = snapshot
    elif getattr(getattr(request, "state", None), "admin_auth_unavailable", False):
        return JSONResponse(
            {"ok": False, "error": "admin_auth_unavailable"},
            status_code=503,
        )
    elif db_provider is not None:
        try:
            persisted_accounts, bootstrap_disabled = (
                db_provider().admin_auth_snapshot()
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not load persisted admin credentials")
            return JSONResponse(
                {"ok": False, "error": "admin_auth_unavailable"},
                status_code=503,
            )
    expected = expected_token()
    configured: list[dict[str, Any]] = []
    raw = os.getenv("ADMIN_API_TOKENS_JSON", "").strip()
    if raw and not bootstrap_disabled:
        try:
            records = json.loads(raw)
        except json.JSONDecodeError:
            logger.error("ADMIN_API_TOKENS_JSON is not valid JSON")
            return JSONResponse(
                {"ok": False, "error": "api_token_configuration_invalid"},
                status_code=503,
            )
        if not isinstance(records, list):
            return JSONResponse(
                {"ok": False, "error": "api_token_configuration_invalid"},
                status_code=503,
            )
        for entry in records:
            if not isinstance(entry, dict):
                continue
            token = entry.get("token")
            role = str(entry.get("role") or "")
            if not isinstance(token, str) or not token.strip():
                continue
            if role not in ("viewer", "reviewer", "operator", "owner"):
                continue
            admin_id = entry.get("admin_id", 0)
            if not isinstance(admin_id, int) or isinstance(admin_id, bool):
                admin_id = 0
            configured.append({
                "token": token.strip(),
                "role": role,
                "admin_id": admin_id,
            })
    if expected and not bootstrap_disabled:
        configured.append({"token": expected, "role": "owner", "admin_id": 0})
    configured.extend(
        {
            "token_hash": account.get("token_hash"),
            "role": account.get("role"),
            "permissions": account.get("permissions"),
            "admin_id": account.get("admin_id", 0),
            "account_id": account.get("id"),
        }
        for account in persisted_accounts
        if account.get("active") is True
        and account.get("role") in ("viewer", "reviewer", "operator", "owner")
        and isinstance(account.get("token_hash"), str)
    )
    now = int(time.time())
    for account in persisted_accounts:
        if (
            account.get("active") is not True
            or account.get("role") not in ("viewer", "reviewer", "operator", "owner")
        ):
            continue
        sessions = account.get("sessions")
        if not isinstance(sessions, list):
            continue
        for session in sessions:
            if (
                isinstance(session, dict)
                and isinstance(session.get("token_hash"), str)
                and isinstance(session.get("expires_at"), int)
                and session["expires_at"] > now
            ):
                configured.append({
                    "token_hash": session["token_hash"],
                    "role": account["role"],
                    "permissions": account.get("permissions"),
                    "admin_id": account.get("admin_id", 0),
                    "account_id": account.get("id"),
                    "session": True,
                })
    if not configured and not expected and not raw:
        return JSONResponse(
            {"ok": False, "error": "api_token_not_configured"}, status_code=503
        )
    path = getattr(getattr(request, "url", None), "path", "")
    authorization = request.headers.get("authorization") or ""
    bearer_token = (
        authorization[7:].strip()
        if authorization[:7].lower() == "bearer "
        else ""
    )
    file_endpoint = (
        path.startswith("/api/admin/photo/")
        or path.startswith("/api/admin/export/")
        or path == "/api/admin/backup/download"
    )
    got = bearer_token if file_endpoint else (
        bearer_token or request.headers.get("x-admin-token") or ""
    )
    token_hash = _token_digest(got) if got else ""
    principal = next(
        (
            item
            for item in configured
            if (
                token_ok(got, item["token"])
                if "token" in item
                else secrets.compare_digest(
                    token_hash, str(item.get("token_hash") or "")
                )
            )
        ),
        None,
    )
    if principal is None:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    method = getattr(request, "method", "POST").upper()
    if path.startswith("/api/admin/notifications") or path == "/api/admin/events/stream":
        permission = "read"
    elif path.startswith("/api/admin/export/") or path.startswith("/api/admin/backup/"):
        permission = "export"
    elif method in ("GET", "HEAD", "OPTIONS"):
        permission = "read"
    elif method == "DELETE" and path.startswith("/api/admin/reports/"):
        permission = "delete"
    elif path.endswith(("/status", "/type", "/note", "/reply", "/workflow", "/followup")):
        permission = "review"
    elif path.startswith("/api/admin/users/"):
        permission = "users"
    else:
        permission = "operate"

    configured_permissions = principal.get("permissions")
    if configured_permissions is None:
        allowed_permissions = ROLE_PERMISSIONS[principal["role"]]
    elif (
        isinstance(configured_permissions, list)
        and all(
            isinstance(item, str) and item in ADMIN_PERMISSIONS
            for item in configured_permissions
        )
    ):
        allowed_permissions = set(configured_permissions)
    else:
        allowed_permissions = set()
    if principal["role"] == "owner":
        allowed_permissions = ADMIN_PERMISSIONS
    if permission not in allowed_permissions:
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    try:
        request.state.admin_role = principal["role"]
        request.state.admin_id = principal["admin_id"]
        request.state.admin_account_id = principal.get("account_id")
        request.state.admin_session_hash = (
            token_hash if principal.get("session") else None
        )
    except AttributeError:
        pass
    return None


def _admin_actor(request) -> int:
    return int(getattr(getattr(request, "state", None), "admin_id", 0) or 0)


def _admin_account_label(request) -> str:
    account_id = getattr(getattr(request, "state", None), "admin_account_id", None)
    return f"account={account_id}" if account_id else "environment-token"


def _owner_only(request):
    if getattr(getattr(request, "state", None), "admin_role", "") != "owner":
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    return None


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
    from app import main as bot_main  # noqa: PLC0415 — ضمن الدالة لتجنب الدوران

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


def _publication_delivery_text(caption: str, media_type: str) -> str:
    if media_type:
        return f"📨 منشور من الجهة المختصة\n\n{caption}".strip()
    return texts.BROADCAST_NOTIF.format(text=utils.esc(caption))


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


async def _send_direct_message_result(user_id: int, text: str) -> dict[str, Any]:
    async def _sender():
        bot, loop = _bot_runtime()
        if bot is None:
            return {"sent": False, "error_code": "bot_not_ready"}
        try:
            message = await bot.send_message(
                chat_id=int(user_id),
                text=f"📨 رسالة من الجهة المختصة:\n\n{text}",
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


async def _send_direct_message(user_id: int, text: str) -> bool:
    result = await _send_direct_message_result(user_id, text)
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
                text="✅ تم رفع الحظر عن حسابك. يمكنك استخدام البوت وتقديم البلاغات من جديد.",
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
           STATUS_APPROVED: "approved", STATUS_REJECTED: "rejected"}.get(new_status)
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
        from app import main as bot_main  # noqa: PLC0415

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

    @app.get("/api/admin/events/stream")
    async def events_stream(request: Request):
        refuse = _auth(request)
        if refuse:
            return refuse
        import queue

        db = db_of()
        subscriber = db.subscribe_notifications()

        async def events():
            try:
                yield ": connected\n\n"
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.to_thread(subscriber.get, True, 15)
                    except queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    yield (
                        f"id: {event['id']}\n"
                        f"event: notification\n"
                        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    )
            finally:
                db.unsubscribe_notifications(subscriber)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/admin/notifications")
    async def list_notifications(
        request: Request,
        unread: bool = False,
        limit: int = 20,
        kind: str | None = None,
    ):
        refuse = _auth(request)
        if refuse:
            return refuse
        if limit < 1 or limit > 100:
            return bad("bad_limit")
        if kind not in (None, "regular", "heartbeat"):
            return bad("bad_notification_kind")
        items, unread_count = await db_of().list_notifications(
            unread_only=unread, limit=limit, kind=kind
        )
        return JSONResponse({
            "ok": True,
            "items": items,
            "unread_count": unread_count,
        })

    @app.get("/api/admin/notifications/stats")
    async def notification_stats(request: Request):
        refuse = _auth(request)
        if refuse:
            return refuse
        return JSONResponse({"ok": True, **await db_of().notification_stats()})

    @app.post("/api/admin/notifications/cleanup")
    async def cleanup_notifications(request: Request):
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        db = db_of()
        deleted = await db.cleanup_notifications()
        await db.audit(
            _admin_actor(request),
            "notifications_cleanup",
            "notifications",
            f"deleted={deleted}",
        )
        return JSONResponse({"ok": True, "deleted": deleted})

    @app.post("/api/admin/notifications/clear")
    async def clear_notifications(request: Request):
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        db = db_of()
        deleted = await db.clear_notifications()
        await db.audit(
            _admin_actor(request),
            "notifications_clear",
            "notifications",
            f"deleted={deleted}",
        )
        return JSONResponse({"ok": True, "deleted": deleted})

    @app.post("/api/admin/notifications/{notification_id}/read")
    async def read_notification(notification_id: int, request: Request):
        refuse = _auth(request)
        if refuse:
            return refuse
        changed = await db_of().mark_notification_read(notification_id)
        if not changed:
            return bad("notification_not_found", 404)
        return JSONResponse({"ok": True})

    @app.post("/api/admin/notifications/read-all")
    async def read_all_notifications(request: Request):
        refuse = _auth(request)
        if refuse:
            return refuse
        count = await db_of().mark_all_notifications_read()
        return JSONResponse({"ok": True, "updated": count})

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
    @app.post("/api/admin/login")
    async def admin_login(request: Request, body: dict[str, Any]) -> JSONResponse:
        credential = body.get("credential")
        if (
            not isinstance(credential, str)
            or not credential
            or len(credential) > 256
        ):
            return bad("bad_credentials", 401)

        accounts, bootstrap_disabled = getattr(
            request.state, "admin_auth_snapshot", ([], False)
        )
        expected = expected_token()
        if expected and not bootstrap_disabled and token_ok(credential, expected):
            return JSONResponse({"ok": True, "token": credential})

        raw = os.getenv("ADMIN_API_TOKENS_JSON", "").strip()
        if raw and not bootstrap_disabled:
            try:
                configured_tokens = json.loads(raw)
            except json.JSONDecodeError:
                logger.error("ADMIN_API_TOKENS_JSON is not valid JSON")
                return JSONResponse(
                    {"ok": False, "error": "api_token_configuration_invalid"},
                    status_code=503,
                )
            if not isinstance(configured_tokens, list):
                return JSONResponse(
                    {"ok": False, "error": "api_token_configuration_invalid"},
                    status_code=503,
                )
            if any(
                isinstance(item, dict)
                and isinstance(item.get("token"), str)
                and item.get("role") in ("viewer", "reviewer", "operator", "owner")
                and token_ok(credential, item["token"])
                for item in configured_tokens
            ):
                return JSONResponse({"ok": True, "token": credential})

        credential_hash = _token_digest(credential)
        account = next(
            (
                item
                for item in accounts
                if item.get("active") is True
                and isinstance(item.get("token_hash"), str)
                and secrets.compare_digest(credential_hash, item["token_hash"])
            ),
            None,
        )
        if account is not None:
            return JSONResponse({"ok": True, "token": credential})

        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return bad("unauthorized", 401)
        name = name.strip().casefold()
        account = next(
            (
                item
                for item in accounts
                if item.get("active") is True
                and str(item.get("name") or "").strip().casefold() == name
                and _password_matches(item, credential)
            ),
            None,
        )
        if account is None:
            return bad("unauthorized", 401)

        session_token = secrets.token_urlsafe(32)
        session_hash = _token_digest(session_token)
        now = int(time.time())
        account_id = account.get("id")

        def append_session(current_accounts, _bootstrap_disabled):
            current = next(
                (
                    item
                    for item in current_accounts
                    if item.get("id") == account_id and item.get("active") is True
                ),
                None,
            )
            if current is None or not _password_matches(current, credential):
                return False
            sessions = current.get("sessions")
            if not isinstance(sessions, list):
                sessions = []
            sessions = [
                item
                for item in sessions
                if isinstance(item, dict)
                and isinstance(item.get("expires_at"), int)
                and item["expires_at"] > now
            ]
            sessions.append({
                "token_hash": session_hash,
                "expires_at": now + ADMIN_SESSION_TTL_SECONDS,
            })
            current["sessions"] = sessions[-ADMIN_MAX_SESSIONS:]
            return True

        db = getattr(request.app.state, "admin_db_provider", lambda: None)()
        if db is None:
            return JSONResponse(
                {"ok": False, "error": "admin_auth_unavailable"}, status_code=503
            )
        issued = await asyncio.to_thread(db.mutate_admin_auth_snapshot, append_session)
        if issued is False:
            return bad("unauthorized", 401)
        return JSONResponse({"ok": True, "token": session_token})

    @app.get("/api/admin/session")
    async def admin_session(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        role = getattr(request.state, "admin_role", "owner")
        return JSONResponse({
            "ok": True,
            "role": role,
            "admin_id": _admin_actor(request),
        })

    @app.get("/api/admin/admins")
    async def list_admins(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        accounts, bootstrap_disabled = request.state.admin_auth_snapshot
        return JSONResponse({
            "ok": True,
            "bootstrap_disabled": bootstrap_disabled,
            "items": [
                {
                    "id": account.get("id"),
                    "name": account.get("name"),
                    "admin_id": account.get("admin_id"),
                    "role": account.get("role"),
                    "permissions": account.get(
                        "permissions", sorted(ROLE_PERMISSIONS.get(account.get("role"), set()))
                    ),
                    "active": account.get("active") is True,
                    "created_at": account.get("created_at"),
                }
                for account in accounts
            ],
        })

    @app.post("/api/admin/admins")
    async def create_admin(request: Request, body: dict[str, Any]) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        name = str(body.get("name") or "").strip()
        role = str(body.get("role") or "")
        admin_id = body.get("admin_id")
        requested_permissions = body.get("permissions")
        if not name or len(name) > 80 or any(ord(char) < 32 for char in name):
            return bad("bad_admin_name")
        if role not in ("viewer", "reviewer", "operator", "owner"):
            return bad("bad_admin_role")
        password = body.get("password")
        if (
            not isinstance(password, str)
            or len(password) < 12
            or len(password) > 128
            or any(ord(char) < 32 for char in password)
        ):
            return bad("bad_admin_password")
        if requested_permissions is None:
            requested_permissions = sorted(ROLE_PERMISSIONS[role])
        if (
            not isinstance(requested_permissions, list)
            or any(
                not isinstance(item, str) or item not in ADMIN_PERMISSIONS
                for item in requested_permissions
            )
            or "read" not in requested_permissions
        ):
            return bad("bad_admin_permissions")
        permissions = (
            sorted(ADMIN_PERMISSIONS)
            if role == "owner"
            else sorted(set(requested_permissions))
        )
        if admin_id is not None and (
            not isinstance(admin_id, int)
            or isinstance(admin_id, bool)
            or admin_id <= 0
        ):
            return bad("bad_admin_id")
        db = db_of()
        accounts, _ = request.state.admin_auth_snapshot
        account = {
            "id": uuid.uuid4().hex,
            "name": name,
            "admin_id": admin_id or 0,
            "role": role,
            "permissions": permissions,
            "active": True,
            **_password_credentials(password),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

        create_outcome = "created"

        def append_account(accounts, _bootstrap_disabled):
            nonlocal create_outcome
            if any(
                str(existing.get("name") or "").strip().casefold()
                == name.casefold()
                for existing in accounts
            ):
                create_outcome = "name_exists"
                return False
            accounts.append(account)
            return True

        await asyncio.to_thread(
            db.mutate_admin_auth_snapshot,
            append_account,
            audit_event=(
                _admin_actor(request),
                "admin_created",
                account["id"],
                f"name={name}; role={role}; by={_admin_account_label(request)}",
            ),
        )
        if create_outcome == "name_exists":
            return bad("admin_name_exists", 409)
        return JSONResponse({
            "ok": True,
            "item": {
                "id": account["id"],
                "name": account["name"],
                "admin_id": account["admin_id"],
                "role": account["role"],
                "permissions": account["permissions"],
                "active": True,
                "created_at": account["created_at"],
            },
        })

    @app.put("/api/admin/admins/{account_id}")
    async def update_admin(
        account_id: str, request: Request, body: dict[str, Any]
    ) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        role = str(body.get("role") or "")
        active = body.get("active")
        requested_permissions = body.get("permissions")
        if role not in ("viewer", "reviewer", "operator", "owner"):
            return bad("bad_admin_role")
        if requested_permissions is None:
            requested_permissions = sorted(ROLE_PERMISSIONS[role])
        if (
            not isinstance(requested_permissions, list)
            or any(
                not isinstance(item, str) or item not in ADMIN_PERMISSIONS
                for item in requested_permissions
            )
            or "read" not in requested_permissions
        ):
            return bad("bad_admin_permissions")
        if not isinstance(active, bool):
            return bad("bad_admin_status")
        password = body.get("password")
        if password is not None and (
            not isinstance(password, str)
            or len(password) < 12
            or len(password) > 128
            or any(ord(char) < 32 for char in password)
        ):
            return bad("bad_admin_password")
        db = db_of()
        outcome = "updated"

        def apply_update(accounts, bootstrap_disabled):
            nonlocal outcome
            account = next(
                (item for item in accounts if item.get("id") == account_id),
                None,
            )
            if account is None:
                outcome = "missing"
                return False
            if account.get("role") == "owner" and account.get("active") is True:
                remaining_owners = sum(
                    1
                    for item in accounts
                    if item.get("role") == "owner"
                    and item.get("active") is True
                    and item.get("id") != account_id
                )
                bootstrap_enabled = (
                    not bootstrap_disabled and _environment_owner_configured()
                )
                if (
                    (role != "owner" or not active)
                    and not remaining_owners
                    and not bootstrap_enabled
                ):
                    outcome = "last_owner"
                    return False
            account["role"] = role
            account["permissions"] = (
                sorted(ADMIN_PERMISSIONS)
                if role == "owner"
                else sorted(set(requested_permissions))
            )
            account["active"] = active
            if password is not None:
                account.update(_password_credentials(password))
                account.pop("token_hash", None)
                account["sessions"] = []
            elif not active:
                account["sessions"] = []
            return True

        await asyncio.to_thread(
            db.mutate_admin_auth_snapshot,
            apply_update,
            audit_event=(
                _admin_actor(request),
                "admin_updated",
                account_id,
                f"role={role}; active={active}; "
                f"password_changed={password is not None}; "
                f"by={_admin_account_label(request)}",
            ),
        )
        if outcome == "missing":
            return bad("admin_not_found", 404)
        if outcome == "last_owner":
            return bad("last_owner_cannot_be_removed", 409)
        return JSONResponse({"ok": True})

    @app.post("/api/admin/admin-token/rotate")
    async def rotate_admin_token(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        db = db_of()
        token = secrets.token_urlsafe(32)
        account = {
            "id": uuid.uuid4().hex,
            "name": "المالك الرئيسي",
            "admin_id": _admin_actor(request),
            "role": "owner",
            "active": True,
            "token_hash": _token_digest(token),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

        def rotate_accounts(accounts, _bootstrap_disabled):
            for existing in accounts:
                if existing.get("role") == "owner":
                    existing["active"] = False
            accounts.append(account)
            return True

        await asyncio.to_thread(
            db.mutate_admin_auth_snapshot,
            rotate_accounts,
            disable_bootstrap=True,
            audit_event=(
                _admin_actor(request),
                "admin_token_rotated",
                account["id"],
                f"by={_admin_account_label(request)}",
            ),
        )
        return JSONResponse({"ok": True, "token": token})

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
    @app.get("/api/admin/reports")
    async def reports(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        db = db_of()
        qp = request.query_params
        try:
            priority = qp.get("priority") or None
            if priority and priority not in ("low", "normal", "high", "urgent"):
                return bad("bad_query")
            if qp.get("overdue", "false") not in ("true", "false"):
                return bad("bad_query")
            if qp.get("unassigned", "false") not in ("true", "false"):
                return bad("bad_query")
            if qp.get("unassigned") == "true" and qp.get("assigned_to"):
                return bad("bad_query")
            rows, total = await db.list_reports_filtered(
                status=qp.get("status") or None,
                vtype=qp.get("type") or None,
                user_id=int(qp["user_id"]) if qp.get("user_id", "").strip() else None,
                assigned_to=int(qp["assigned_to"]) if qp.get("assigned_to", "").strip() else None,
                unassigned_only=qp.get("unassigned") == "true",
                priority=priority,
                overdue_only=qp.get("overdue") == "true",
                q=qp.get("q") or None,
                page=max(0, int(qp.get("page", "0") or 0)),
                size=min(int(qp.get("size", str(DEFAULT_PAGE_SIZE)) or 20), MAX_PAGE_SIZE),
            )
        except ValueError:
            return bad("bad_query")
        return JSONResponse({
            "ok": True, "total": total,
            "items": [_safe_report(r) for r in rows],
        })

    @app.get("/api/admin/reports/{rid}")
    async def report_detail(rid: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        r = await db_of().get_by_id(rid)
        if not r:
            return bad("not_found", 404)
        return JSONResponse({"ok": True, "item": _safe_report(r)})

    @app.put("/api/admin/reports/{rid}/workflow")
    async def report_workflow(rid: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        body = await _json(request)
        db = db_of()
        report = await db.get_by_id(rid)
        if not report:
            return bad("not_found", 404)

        priority = body.get("priority", report.get("priority") or "normal")
        if priority not in ("low", "normal", "high", "urgent"):
            return bad("bad_priority")

        assigned_to = report.get("assigned_to")
        if "assigned_to" in body:
            raw_assignee = body["assigned_to"]
            if raw_assignee is None or raw_assignee == "":
                assigned_to = None
            else:
                try:
                    assigned_to = int(raw_assignee)
                except (TypeError, ValueError):
                    return bad("bad_assignee")
                from app import config  # noqa: PLC0415
                if assigned_to not in config.ADMIN_IDS:
                    return bad("unknown_assignee")

        due_at = report.get("due_at")
        if "due_at" in body:
            raw_due = body["due_at"]
            if raw_due is None or raw_due == "":
                due_at = None
            elif not isinstance(raw_due, str):
                return bad("bad_due_at")
            else:
                try:
                    parsed_due = datetime.fromisoformat(raw_due.replace("Z", "+00:00"))
                    if parsed_due.tzinfo is None:
                        return bad("due_at_timezone_required")
                    due_at = parsed_due.astimezone(timezone.utc).isoformat(timespec="seconds")
                except ValueError:
                    return bad("bad_due_at")

        ok = await db.update_report_workflow(rid, priority, assigned_to, due_at)
        if not ok:
            return bad("not_found", 404)
        await db.audit(
            _admin_actor(request), "report_workflow_changed", str(rid),
            f"priority={priority}; assigned_to={assigned_to}; due_at={due_at}",
        )
        return JSONResponse({
            "ok": True,
            "priority": priority,
            "assigned_to": assigned_to,
            "due_at": due_at,
        })

    @app.post("/api/admin/reports/{rid}/followup")
    async def request_report_followup(rid: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        body = await _json(request)
        note = str(body.get("note") or "").strip()
        if not 3 <= len(note) <= MAX_TEXT:
            return bad("note_length")
        db = db_of()
        report = await db.get_by_id(rid)
        if not report:
            return bad("not_found", 404)
        if report.get("status") in (STATUS_APPROVED, STATUS_REJECTED):
            return bad("report_closed", 409)
        if not await db.user_contact_enabled(int(report["user_id"])):
            return bad("direct_contact_disabled", 403)
        sent = await _send_direct_message(
            int(report["user_id"]),
            f"نحتاج إلى معلومات إضافية بخصوص البلاغ {report.get('seq') or rid}:\n\n"
            f"{note}\n\nأرسل المعلومات باستخدام الأمر:\n"
            f"/reply {report.get('seq') or rid} نص المعلومات الإضافية",
        )
        if not sent:
            return bad("telegram_send_failed", 502)
        await db.set_followup_request(rid, note)
        await db.audit(
            _admin_actor(request), "report_followup_requested", str(rid), note[:MAX_TEXT]
        )
        return JSONResponse({"ok": True, "sent": True})

    # ------------------------------------------------------------
    # 2) إجراءات البلاغ: الحالة/التصنيف/الملاحظة/الرد/الحذف
    # ------------------------------------------------------------
    @app.post("/api/admin/reports/{rid}/status")
    async def set_status(rid: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        body = await _json(request)
        db = db_of()
        code = str(body.get("status") or "")
        new_status = STATUS_CODES.get(code)
        if not new_status:
            return bad("bad_status")
        reason = (str(body.get("reason") or "").strip())[:MAX_TEXT]
        if new_status == STATUS_REJECTED and not reason:
            return bad("reason_required")

        r = await db.get_by_id(rid)
        if not r:
            return bad("not_found", 404)

        ok = await db.update_status(rid, new_status, _admin_actor(request))
        if not ok:
            return bad("not_found", 404)
        audit_details = f"{new_status} (لوحة الإدارة)"
        if new_status == STATUS_REJECTED:
            audit_details += f" — سبب الرفض: {reason}"
        await db.audit(_admin_actor(request), "status_changed", str(rid),
                       audit_details)

        # الإشعار: الرفض بسبب = دائمًا؛ غيره = مرة واحدة لكل حالة (نفس دفتر تيليغرام)
        note_extra = ""
        if new_status == STATUS_REJECTED and reason:
            note_extra = texts.REJECT_REASON_NOTIF.format(reason=utils.esc(reason))
        notified = await _notify_status(await db.get_by_id(rid), new_status, note_extra)
        return JSONResponse({"ok": True, "notified": notified})

    @app.post("/api/admin/reports/{rid}/type")
    async def set_type(rid: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        body = await _json(request)
        vtype = (str(body.get("violation_type") or "").strip()) or None
        if vtype and len(vtype) > 60:
            return bad("type_too_long")
        ok = await db_of().set_violation_type(rid, vtype)
        if ok:
            await db_of().audit(
                _admin_actor(request), "type_changed", str(rid), vtype or "(فارغ)"
            )
        return OK if ok else bad("not_found", 404)

    @app.post("/api/admin/reports/{rid}/note")
    async def set_note(rid: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        body = await _json(request)
        note = (str(body.get("note") or "").strip())[:MAX_TEXT] or None
        ok = await db_of().set_admin_note(rid, note)
        if ok:
            await db_of().audit(
                _admin_actor(request), "note_changed", str(rid), note or "(فارغ)"
            )
        return OK if ok else bad("not_found", 404)

    @app.post("/api/admin/reports/{rid}/reply")
    async def reply_reporter(rid: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        body = await _json(request)
        text = (str(body.get("text") or "").strip())
        if not 2 <= len(text) <= MAX_TEXT:
            return bad("text_length")
        db = db_of()
        r = await db.get_by_id(rid)
        if not r:
            return bad("not_found", 404)
        sent = await _send_panel_reply(r, text)
        await db.audit(_admin_actor(request), "panel_reply", str(rid),
                       text[:120] + ("…" if len(text) > 120 else ""))
        if not sent:
            return bad("bot_not_ready", 503)
        return JSONResponse({"ok": True})

    @app.delete("/api/admin/reports/{rid}")
    async def delete_report(rid: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        body = await _json(request)
        db = db_of()
        r = await db.get_by_id(rid)
        if not r:
            return bad("not_found", 404)
        if str(body.get("confirm_seq") or "").strip() != str(r.get("seq") or ""):
            return bad("must_confirm_seq")
        ok = await db.delete_report(rid)
        if ok:
            await db.audit(_admin_actor(request), "report_deleted", str(r.get("seq") or str(rid)),
                           f"المستخدم {r.get('user_id')}")
        return OK if ok else bad("not_found", 404)

    # ------------------------------------------------------------
    # 3) المستخدمون: قائمة + بطاقة + حجب/رفع حجب
    # ------------------------------------------------------------
    @app.get("/api/admin/users")
    async def users(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        qp = request.query_params
        try:
            rows, total = await db_of().list_users(
                q=qp.get("q") or None,
                page=max(0, int(qp.get("page", "0") or 0)),
                size=min(int(qp.get("size", str(DEFAULT_PAGE_SIZE)) or 20), MAX_PAGE_SIZE),
            )
        except ValueError:
            return bad("bad_query")
        return JSONResponse({"ok": True, "total": total, "items": rows})

    @app.get("/api/admin/users/{uid}")
    async def user_detail(uid: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        db = db_of()
        profile = await db.user_profile(uid)
        if not profile:
            return bad("not_found", 404)
        reports, total = await db.list_reports_filtered(user_id=uid, size=MAX_PAGE_SIZE)
        return JSONResponse({
            "ok": True, "profile": profile,
            "reports_total": total, "reports": [_safe_report(r) for r in reports],
        })

    @app.get("/api/admin/users/{uid}/dossier")
    async def user_dossier(uid: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        db = db_of()
        dossier = await db.user_dossier(uid)
        if not dossier:
            return bad("not_found", 404)
        await db.audit(
            _admin_actor(request),
            "user_dossier_viewed",
            str(uid),
            "فتح بطاقة الكشف الشامل",
        )
        return JSONResponse({
            "ok": True,
            "profile": dossier["profile"],
            "reports": [_safe_report(row) for row in dossier["reports"]],
            "reports_total": dossier["reports_total"],
            "audit": dossier["audit"],
            "audit_total": dossier["audit_total"],
            "audit_truncated": dossier["audit_truncated"],
            "scope": "submitted_reports_only",
        })

    @app.post("/api/admin/users/{uid}/block")
    async def block_user(uid: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        body = await _json(request)
        reason = (str(body.get("reason") or "").strip())[:MAX_TEXT] or "بدون سبب موثق"
        duration = str(body.get("duration") or "permanent").strip().lower()
        if duration == "permanent":
            until, kind = FAR_FUTURE, "حظر دائم"
        else:
            try:
                hours = float(duration)
            except ValueError:
                return bad("bad_duration")
            if not 0 < hours <= 24 * 365:
                return bad("bad_duration")
            until = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(
                timespec="seconds"
            )
            kind = f"تجميد مؤقت حتى {utils.fmt_local(until)}"
        db = db_of()
        block = await db.get_block(uid) or {}
        await db.save_block(uid, int(block.get("strikes") or 0), until, reason)
        await db.audit(
            _admin_actor(request), "user_blocked", str(uid), f"{kind} — {reason}"
        )
        await _send_block_notice(uid, kind, reason, db)
        return JSONResponse({"ok": True, "frozen_until": until})

    @app.post("/api/admin/users/{uid}/unblock")
    async def unblock_user(uid: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        db = db_of()
        previous_block = await db.get_block(uid) or {}
        await db.clear_block(uid)
        notified = await _send_unblock_notice(uid)
        previous_reason = str(previous_block.get("reason") or "غير مسجل")
        previous_until = str(previous_block.get("frozen_until") or "غير محجوب")
        await db.audit(
            _admin_actor(request),
            "user_unblocked",
            str(uid),
            f"رفع شامل لقيود المستخدم؛ الحظر السابق: {previous_reason} ({previous_until})"
            + (" — أُرسل إشعار للمستخدم" if notified else " — تعذر إرسال الإشعار"),
        )
        return JSONResponse({"ok": True, "notified": notified})

    @app.get("/api/admin/blocked-users")
    async def blocked_users(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        qp = request.query_params
        try:
            page = max(0, int(qp.get("page", "0") or 0))
            size = min(int(qp.get("size", "50") or 50), MAX_PAGE_SIZE)
            user_id = int(qp["user_id"]) if qp.get("user_id") else None
            if size < 1:
                return bad("bad_query")
        except ValueError:
            return bad("bad_query")
        items, total = await db_of().list_blocked_users(
            page=page, size=size, user_id=user_id
        )
        return JSONResponse({
            "ok": True, "page": page, "size": size, "total": total, "items": items,
        })

    @app.get("/api/admin/blocked-users/{uid}/photo")
    async def blocked_user_photo(uid: int, request: Request) -> Any:
        refuse = _auth(request)
        if refuse:
            return refuse
        db = db_of()
        blocked, _ = await db.list_blocked_users(page=0, size=1, user_id=uid)
        if not blocked:
            return bad("not_found", 404)
        evidence = await db.get_block_evidence(uid)
        if not evidence:
            return bad("no_photo", 404)
        download = request.query_params.get("download") == "true"
        local = _local_photo_path(str(evidence.get("photo_path") or "").strip())
        if local is not None and local.is_file():
            media_type = (
                mimetypes.guess_type(local.name)[0] or "application/octet-stream"
            )
            if not media_type.startswith("image/"):
                media_type = "application/octet-stream"
            return FileResponse(
                str(local),
                media_type=media_type,
                filename=(
                    f"blocked-{uid}-evidence{local.suffix.lower()}"
                    if download
                    else None
                ),
                headers={"Cache-Control": "private, no-store"},
            )
        file_id = evidence.get("photo_file_id")
        if not file_id:
            return bad("photo_unavailable", 503)
        data = await _photo_bytes(str(file_id))
        if not data:
            return bad("photo_unavailable", 503)
        media_type = _image_media_type(data)
        headers = {"Cache-Control": "private, no-store"}
        if download:
            extension = mimetypes.guess_extension(media_type) or ".jpg"
            headers["Content-Disposition"] = (
                f'attachment; filename="blocked-{uid}-evidence{extension}"'
            )
        return Response(content=data, media_type=media_type, headers=headers)

    @app.get("/api/admin/blocked-users/{uid}/report")
    async def blocked_user_report(uid: int, request: Request) -> Any:
        refuse = _auth(request)
        if refuse:
            return refuse
        db = db_of()
        blocked, _ = await db.list_blocked_users(page=0, size=1, user_id=uid)
        if not blocked:
            return bad("not_found", 404)
        item = blocked[0]
        lines = [
            "تقرير حجب مستخدم",
            f"معرّف المستخدم: {uid}",
            f"الاسم: {item.get('full_name') or '-'}",
            (
                f"اسم المستخدم: @{item['username']}"
                if item.get("username")
                else "اسم المستخدم: -"
            ),
            f"سبب الحجب: {item.get('reason') or 'غير مسجل'}",
            f"عدد المخالفات: {item.get('strikes', 0)}",
            f"الحجب حتى: {item.get('frozen_until') or 'غير محدد'}",
            f"آخر تحديث: {item.get('updated_at') or '-'}",
            f"صورة دليل مرفقة: {'نعم' if item.get('has_evidence_photo') else 'لا'}",
        ]
        report_id = item.get("report_id")
        if report_id is not None:
            report = await db.get_by_id(int(report_id))
            if report:
                lines.extend([
                    "",
                    "آخر بلاغ مسجل للمستخدم (مرجع إضافي)",
                    f"رقم البلاغ: {report.get('seq') or report_id}",
                    f"تاريخ البلاغ: {report.get('created_at') or '-'}",
                    (
                        "التصنيف: "
                        f"{report.get('category') or report.get('violation_type') or '-'}"
                    ),
                    f"الوصف: {report.get('description') or '-'}",
                ])
        return Response(
            content="\n".join(lines),
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="blocked-user-{uid}-report.txt"'
                ),
                "Cache-Control": "private, no-store",
            },
        )

    @app.post("/api/admin/users/{uid}/message")
    async def message_user(uid: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        body = await _json(request)
        text = str(body.get("text") or "").strip()
        if not 2 <= len(text) <= MAX_TEXT:
            return bad("text_length")
        db = db_of()
        if not await db.user_profile(uid):
            return bad("not_found", 404)
        if not await db.user_contact_enabled(uid):
            return bad("direct_contact_disabled", 403)
        await db.open_user_conversation(uid)
        result = await _send_direct_message_result(uid, text)
        sent = result.get("sent") is True
        telegram_id = (
            int(result["message_id"])
            if isinstance(result.get("message_id"), int)
            else None
        )
        logged_message = (
            await db.get_user_message_by_telegram_id(uid, telegram_id)
            if sent and telegram_id is not None else None
        )
        message_id = int(logged_message["id"]) if logged_message else (
            await db.add_user_message(
                uid,
                "out",
                f"📨 رسالة من الجهة المختصة:\n\n{text}" if sent else text,
                _admin_actor(request),
                delivery_status="delivered" if sent else "failed",
                error_code=None if sent else str(
                    result.get("error_code") or "telegram_send_failed"
                ),
                telegram_message_id=telegram_id,
            )
        )
        if not sent:
            error_code = str(result.get("error_code") or "telegram_send_failed")
            await db.audit(
                _admin_actor(request),
                "user_message_failed",
                str(uid),
                f"message_id={message_id}; error={error_code}",
            )
            return JSONResponse(
                {
                    "ok": False,
                    "sent": False,
                    "message_id": message_id,
                    "error": error_code,
                },
                status_code=502,
            )
        await db.audit(
            _admin_actor(request),
            "user_message",
            str(uid),
            f"message_id={message_id}; sent",
        )
        return JSONResponse({"ok": True, "sent": True, "message_id": message_id})

    @app.post("/api/admin/users/{uid}/media")
    async def message_user_media(uid: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        db = db_of()
        if not await db.user_profile(uid):
            return bad("not_found", 404)
        if not await db.user_contact_enabled(uid):
            return bad("direct_contact_disabled", 403)
        try:
            form = await request.form()
        except Exception:
            logger.exception("تعذر قراءة وسيط الرسالة المباشرة")
            return bad("multipart_unavailable", 503)
        media_type = str(form.get("media_type") or "").strip()
        caption = str(form.get("caption") or "").strip()
        upload = form.get("media")
        if media_type not in {"photo", "video", "audio", "voice", "document"}:
            return bad("bad_media_type")
        if len(caption) > 1000:
            return bad("caption_too_long")
        if not hasattr(upload, "read"):
            return bad("media_required")
        filename = os.path.basename(str(getattr(upload, "filename", "") or "attachment"))
        filename = filename.replace("\x00", "")[:180] or "attachment"
        limit = 10 * 1024 * 1024 if media_type == "photo" else 20 * 1024 * 1024
        media_data = await upload.read(limit + 1)
        if len(media_data) > limit:
            return bad("media_too_large", 413)
        if not media_data:
            return bad("empty_media")
        if media_type == "photo" and _image_media_type(media_data, filename) not in {
            "image/jpeg", "image/png",
        }:
            return bad("unsupported_photo_format")
        if media_type == "video" and (
            len(media_data) < 12 or media_data[4:8] != b"ftyp"
        ):
            return bad("invalid_video_format")
        if media_type == "voice" and not filename.lower().endswith((".ogg", ".oga")):
            return bad("invalid_voice_format")

        from telegram import InputFile  # noqa: PLC0415
        from telegram.error import BadRequest, Forbidden, NetworkError  # noqa: PLC0415

        bot, _loop = _bot_runtime()
        if bot is None:
            return bad("bot_not_ready", 503)
        method = {
            "photo": "send_photo",
            "video": "send_video",
            "audio": "send_audio",
            "voice": "send_voice",
            "document": "send_document",
        }[media_type]
        field = {
            "photo": "photo",
            "video": "video",
            "audio": "audio",
            "voice": "voice",
            "document": "document",
        }[media_type]

        async def _send():
            telegram_method = getattr(bot, method)
            return await telegram_method(
                chat_id=uid,
                caption=caption or None,
                **{field: InputFile(media_data, filename=filename)},
            )

        try:
            message = await _on_bot_loop_value(_send)
        except Forbidden:
            return bad("user_blocked_bot", 502)
        except BadRequest:
            return bad("recipient_unavailable", 502)
        except NetworkError:
            return bad("telegram_network_error", 502)
        except Exception:
            logger.exception("تعذر إرسال وسيط إلى المستخدم %s", uid)
            return bad("telegram_send_failed", 502)
        if message is None:
            return bad("bot_not_ready", 503)
        media_obj = getattr(message, media_type, None)
        if media_type == "photo":
            photos = getattr(message, "photo", None) or []
            media_obj = photos[-1] if photos else None
        await db.open_user_conversation(uid)
        telegram_id = getattr(message, "message_id", None)
        logged_message = (
            await db.get_user_message_by_telegram_id(uid, int(telegram_id))
            if isinstance(telegram_id, int) else None
        )
        message_id = int(logged_message["id"]) if logged_message else (
            await db.add_user_message(
                uid,
                "out",
                caption or f"[وسيط: {media_type}]",
                _admin_actor(request),
                media_type=media_type,
                telegram_file_id=getattr(media_obj, "file_id", None),
                telegram_message_id=telegram_id,
                filename=filename,
            )
        )
        await db.audit(
            _admin_actor(request),
            "user_media_message",
            str(uid),
            f"message_id={message_id}; type={media_type}",
        )
        return JSONResponse({"ok": True, "sent": True, "message_id": message_id})

    @app.get("/api/admin/conversations")
    async def conversations(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        try:
            limit = min(int(request.query_params.get("limit", "100") or 100), 200)
            if limit < 1:
                return bad("bad_query")
        except ValueError:
            return bad("bad_query")
        items = await db_of().list_user_conversations(
            request.query_params.get("q"), limit
        )
        return JSONResponse({"ok": True, "items": items})

    @app.get("/api/admin/users/{uid}/messages")
    async def user_messages(uid: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        try:
            after_id = max(0, int(request.query_params.get("after_id", "0") or 0))
            limit = min(int(request.query_params.get("limit", "100") or 100), 200)
            if limit < 1:
                return bad("bad_query")
        except ValueError:
            return bad("bad_query")
        db = db_of()
        if not await db.user_profile(uid):
            return bad("not_found", 404)
        return JSONResponse({
            "ok": True,
            "can_manage_messages": (
                getattr(request.state, "admin_role", "") == "owner"
            ),
            "items": [
                {
                    key: value
                    for key, value in item.items()
                    if key != "telegram_file_id"
                }
                | {"has_media": bool(item.get("telegram_file_id"))}
                for item in await db.list_user_messages(uid, after_id, limit)
            ],
        })

    @app.get("/api/admin/users/{uid}/messages/{message_id}/media")
    async def get_user_message_media(
        uid: int, message_id: int, request: Request
    ) -> Response:
        refuse = _auth(request)
        if refuse:
            return refuse
        messages = await db_of().list_user_messages(uid, max(0, message_id - 1), 1)
        if not messages or int(messages[0]["id"]) != message_id:
            return bad("not_found", 404)
        item = messages[0]
        file_id = str(item.get("telegram_file_id") or "")
        if not file_id:
            return bad("media_not_available", 404)
        bot, _loop = _bot_runtime()
        if bot is None:
            return bad("bot_not_ready", 503)

        async def _download():
            telegram_file = await bot.get_file(file_id)
            return await telegram_file.download_as_bytearray()

        try:
            data = await _on_bot_loop_value(_download)
        except Exception:
            logger.exception("تعذر تنزيل وسيط المحادثة %s للمستخدم %s", message_id, uid)
            return bad("media_download_failed", 502)
        if data is None:
            return bad("bot_not_ready", 503)
        media_type = str(item.get("media_type") or "")
        filename = str(item.get("filename") or "")
        guessed = mimetypes.guess_type(filename)[0]
        content_type = guessed or {
            "photo": "image/jpeg",
            "video": "video/mp4",
            "voice": "audio/ogg",
            "audio": "audio/mpeg",
        }.get(media_type, "application/octet-stream")
        return Response(
            content=bytes(data),
            media_type=content_type,
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": (
                    "attachment; filename*=UTF-8''"
                    + urllib.parse.quote(filename or f"message-{message_id}")
                ),
            },
        )

    @app.put("/api/admin/users/{uid}/messages/{message_id}")
    async def edit_user_message(
        uid: int, message_id: int, request: Request
    ) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        body = await _json(request)
        text = str(body.get("text") or "").strip()
        if not 1 <= len(text) <= 1000:
            return bad("text_length")
        db = db_of()
        item = await db.get_outgoing_user_message(uid, message_id)
        if not item:
            return bad("not_found", 404)
        if item.get("is_deleted"):
            return bad("message_deleted", 409)
        telegram_message_id = item.get("telegram_message_id")
        if not telegram_message_id:
            return bad("message_not_editable", 409)
        created = datetime.fromisoformat(str(item["created_at"]))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created < datetime.now(timezone.utc) - timedelta(hours=48):
            return bad("message_expired", 410)
        bot, _loop = _bot_runtime()
        if bot is None:
            return bad("bot_not_ready", 503)
        media_type = str(item.get("media_type") or "")

        async def _edit():
            if media_type:
                return await bot.edit_message_caption(
                    chat_id=uid,
                    message_id=int(telegram_message_id),
                    caption=text,
                )
            return await bot.edit_message_text(
                chat_id=uid,
                message_id=int(telegram_message_id),
                text=text,
            )

        try:
            await _on_bot_loop_value(_edit)
        except Exception:
            logger.exception("تعذر تعديل رسالة المستخدم %s/%s", uid, message_id)
            return bad("telegram_edit_failed", 502)
        await db.edit_user_message_content(uid, message_id, text)
        await db.audit(
            _admin_actor(request), "user_message_edited", str(uid),
            f"message_id={message_id}",
        )
        return JSONResponse({"ok": True})

    @app.delete("/api/admin/users/{uid}/messages/{message_id}")
    async def delete_user_message(
        uid: int, message_id: int, request: Request
    ) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        db = db_of()
        item = await db.get_outgoing_user_message(uid, message_id)
        if not item:
            return bad("not_found", 404)
        if item.get("is_deleted"):
            return bad("message_deleted", 409)
        telegram_message_id = item.get("telegram_message_id")
        if not telegram_message_id:
            return bad("message_not_deletable", 409)
        created = datetime.fromisoformat(str(item["created_at"]))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created < datetime.now(timezone.utc) - timedelta(hours=48):
            return bad("message_expired", 410)
        bot, _loop = _bot_runtime()
        if bot is None:
            return bad("bot_not_ready", 503)
        try:
            await _on_bot_loop_value(
                lambda: bot.delete_message(
                    chat_id=uid, message_id=int(telegram_message_id)
                )
            )
        except Exception:
            logger.exception("تعذر حذف رسالة المستخدم %s/%s", uid, message_id)
            return bad("telegram_delete_failed", 502)
        await db.mark_user_message_changed(uid, message_id, deleted=True)
        await db.audit(
            _admin_actor(request), "user_message_deleted", str(uid),
            f"message_id={message_id}",
        )
        return JSONResponse({"ok": True})

    @app.post("/api/admin/users/{uid}/messages/read")
    async def mark_messages_read(uid: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        body = await _json(request)
        through_message_id = body.get("last_message_id")
        if through_message_id is not None and (
            type(through_message_id) is not int or through_message_id < 0
        ):
            return bad("bad_request")
        db = db_of()
        if not await db.user_profile(uid):
            return bad("not_found", 404)
        last_read_id = await db.mark_user_conversation_read(
            uid, through_message_id
        )
        return JSONResponse({"ok": True, "last_read_message_id": last_read_id})

    @app.post("/api/admin/users/{uid}/messages/{message_id}/retry")
    async def retry_user_message(
        uid: int, message_id: int, request: Request
    ) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        db = db_of()
        if not await db.user_profile(uid):
            return bad("not_found", 404)
        if not await db.user_contact_enabled(uid):
            return bad("direct_contact_disabled", 403)
        rows = await db.list_user_messages(uid, max(0, message_id - 1), 1)
        if not rows or int(rows[0]["id"]) != message_id:
            return bad("not_found", 404)
        message = rows[0]
        if message.get("direction") != "out":
            return bad("message_not_outgoing", 400)
        if message.get("delivery_status") != "failed":
            return bad("message_not_retryable", 409)
        result = await _send_direct_message_result(uid, str(message["text"]))
        sent = result.get("sent") is True
        error_code = None if sent else str(
            result.get("error_code") or "telegram_send_failed"
        )
        if sent:
            telegram_id = (
                int(result["message_id"])
                if isinstance(result.get("message_id"), int) else None
            )
            await db.reconcile_retried_message(uid, message_id, telegram_id)
        else:
            await db.update_user_message_delivery(
                message_id,
                "failed",
                error_code,
            )
        await db.audit(
            _admin_actor(request),
            "user_message_retry",
            str(uid),
            f"message_id={message_id}; "
            f"{'sent' if sent else 'error=' + str(error_code)}",
        )
        if not sent:
            return JSONResponse(
                {
                    "ok": False,
                    "sent": False,
                    "message_id": message_id,
                    "error": error_code,
                },
                status_code=502,
            )
        return JSONResponse({"ok": True, "sent": True, "message_id": message_id})

    @app.put("/api/admin/users/{uid}/contact")
    async def set_user_contact(uid: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        body = await _json(request)
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            return bad("bad_contact_setting")
        db = db_of()
        if not await db.user_profile(uid):
            return bad("not_found", 404)
        changed = await db.set_user_contact_enabled(uid, enabled)
        notified = True
        if changed:
            notice = (
                "تم تفعيل إمكانية التواصل المباشر معك عبر بوت البلاغات."
                if enabled
                else "تم إيقاف إمكانية التواصل المباشر معك عبر بوت البلاغات."
            )
            notified = await _send_direct_message(uid, notice)
        await db.audit(
            _admin_actor(request),
            "user_contact_enabled" if enabled else "user_contact_disabled",
            str(uid),
            f"{'changed' if changed else 'unchanged'}"
            f"{'; notified' if notified else '; notification_failed'}",
        )
        return JSONResponse({
            "ok": True,
            "enabled": enabled,
            "changed": changed,
            "notified": notified,
        })

    @app.get("/api/admin/osint/sources")
    async def osint_sources(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        raw_report_id = request.query_params.get("report_id")
        try:
            report_id = int(raw_report_id) if raw_report_id else None
            limit = min(int(request.query_params.get("limit", "100") or 100), 500)
            if report_id is not None and report_id <= 0 or limit < 1:
                return bad("bad_query")
        except ValueError:
            return bad("bad_query")
        items = await db_of().list_osint_sources(report_id, limit)
        return JSONResponse({"ok": True, "items": items})

    @app.post("/api/admin/osint/sources")
    async def osint_source_add(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        body = await _json(request)
        title = str(body.get("title") or "").strip()
        notes = str(body.get("notes") or "").strip()
        source_level = str(body.get("source_level") or "")
        source_url = _sanitize_public_source_url(str(body.get("url") or ""))
        if not source_url:
            return bad("invalid_public_source_url")
        if not 2 <= len(title) <= 180:
            return bad("title_length")
        if len(notes) > 2000:
            return bad("notes_too_long")
        if source_level not in {"primary", "secondary", "unverified"}:
            return bad("bad_source_level")
        raw_report_id = body.get("report_id")
        if raw_report_id is None:
            report_id = None
        elif isinstance(raw_report_id, int) and not isinstance(raw_report_id, bool):
            report_id = raw_report_id
        else:
            return bad("bad_report_id")
        db = db_of()
        if report_id is not None and not await db.get_by_id(report_id):
            return bad("not_found", 404)
        item = await db.add_osint_source(
            report_id,
            source_url,
            title,
            notes,
            source_level,
            _admin_actor(request),
        )
        await db.audit(
            _admin_actor(request),
            "osint_source_added",
            str(report_id or "general"),
            f"source_id={item['id']}; level={source_level}",
        )
        return JSONResponse({"ok": True, "item": item})

    @app.delete("/api/admin/osint/sources/{source_id}")
    async def osint_source_delete(
        source_id: int, request: Request
    ) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        deleted = await db_of().delete_osint_source(source_id)
        if not deleted:
            return bad("not_found", 404)
        await db_of().audit(
            _admin_actor(request), "osint_source_deleted", str(source_id), ""
        )
        return JSONResponse({"ok": True, "deleted": True})

    # ------------------------------------------------------------
    # 4) سجل التدقيق + التصدير (لتكميل أروقة الالتزام)
    # ------------------------------------------------------------
    @app.get("/api/admin/audit")
    async def audit_api(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        qp = request.query_params
        try:
            limit = min(int(qp.get("limit", "50") or 50), 5000)
        except ValueError:
            return bad("bad_query")
        start, end = qp.get("start"), qp.get("end")
        if start is None and end is None:
            items = await db_of().recent_audit(max(1, min(limit, 200)))
        elif not start or not end:
            return bad("bad_date_range")
        else:
            bounds = _audit_date_range(start, end)
            if bounds is None:
                return bad("bad_date_range")
            items = await db_of().audit_range(*bounds, limit=max(1, limit))
        return JSONResponse({"ok": True, "items": items})

    @app.get("/api/admin/audit/export")
    async def audit_export(request: Request) -> Response:
        refuse = _auth(request)
        if refuse:
            return refuse
        qp = request.query_params
        start, end = qp.get("start", ""), qp.get("end", "")
        bounds = _audit_date_range(start, end)
        if bounds is None:
            return bad("bad_date_range")
        import csv
        import io

        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(("id", "ts", "actor_id", "action", "target", "details"))
        for item in await db_of().audit_range(*bounds):
            writer.writerow(
                item.get(key)
                for key in ("id", "ts", "actor_id", "action", "target", "details")
            )
        return Response(
            output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="audit-{start}-to-{end}.csv"'
                )
            },
        )

    @app.post("/api/admin/audit/cleanup")
    async def audit_cleanup(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        body = await _json(request)
        start = str(body.get("start") or "")
        end = str(body.get("end") or "")
        bounds = _audit_date_range(start, end)
        if bounds is None:
            return bad("bad_date_range")
        db = db_of()
        deleted = await db.delete_audit_range(*bounds)
        await db.audit(
            _admin_actor(request),
            "audit_cleanup",
            f"{start}..{end}",
            f"deleted={deleted}",
        )
        return JSONResponse({"ok": True, "deleted": deleted})

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
            "statuses": [STATUS_NEW, STATUS_REVIEW, STATUS_APPROVED, STATUS_REJECTED],
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
        ids, overflow = await db_of().all_user_ids(cap=100_000)
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
        upload = form.get("media")
        if len(caption) > 900:
            return bad("caption_too_long")
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
            media_data = await upload.read(limit + 1)
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
                text = _publication_delivery_text(caption, media_type)
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
        if len(caption) > 900:
            return bad("caption_too_long")
        if media_type not in {"", "photo", "video", "document"}:
            return bad("bad_media_type")
        if media_type and not file_id:
            return bad("media_preview_required")
        if not media_type and len(caption) < 3:
            return bad("publication_content_required")
        db = db_of()
        ids, overflow = await db.all_user_ids(cap=100_000)
        if overflow:
            return bad("recipient_limit_exceeded", 413)
        if not ids:
            return JSONResponse({"ok": True, "sent": 0, "failed": 0, "total": 0})
        bot, loop = _bot_runtime()
        if bot is None:
            return bad("bot_not_ready", 503)
        actor_id = _admin_actor(request)
        job_id = await db.create_broadcast_job(
            caption or f"منشور وسائط: {media_type}",
            actor_id,
            len(ids),
            media_type or "text",
        )
        if job_id is None:
            return bad("broadcast_in_progress", 409)

        async def _send_to_user(user_id: int) -> None:
            text = _publication_delivery_text(caption, media_type)
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
                    f"type={media_type or 'text'}; sent={sent}; failed={failed}",
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
        return JSONResponse({"ok": True, "items": await db.effective_vtypes()})

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
        if not 3 <= len(clean) <= 12:
            return bad("bad_list")
        db = db_of()
        await db.set_vtypes_override(clean)
        await db.audit(_admin_actor(request), "vtypes_changed", "التصنيفات",
                       " / ".join(clean))
        return JSONResponse({"ok": True, "items": clean})

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
        from app import main as bot_main  # noqa: PLC0415

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
        from app import main as bot_main  # noqa: PLC0415

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
        if not name or len(name) > 64:
            return bad("bad_bot_name")
        if len(short_description) > 120:
            return bad("short_description_too_long")
        if len(description) > 512:
            return bad("description_too_long")
        if not welcome_text or len(welcome_text) > 3500:
            return bad("bad_welcome_text")

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
            "photo_updated_at": (
                datetime.now(timezone.utc).isoformat(timespec="seconds")
                if photo_bytes else str(previous.get("photo_updated_at") or "")
            ),
        }
        await db.set_state(
            "bot_profile", json.dumps(values, ensure_ascii=False, sort_keys=True)
        )
        await db.audit(
            _admin_actor(request),
            "bot_profile_changed",
            "هوية البوت",
            "تحديث بيانات الحساب ورسالة البداية"
            + (" وتغيير الصورة" if photo_bytes else ""),
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

        from app import main as bot_main  # noqa: PLC0415

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
