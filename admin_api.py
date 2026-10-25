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
import json
import logging
import mimetypes
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402

import utils  # noqa: E402
import texts  # noqa: E402
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
FAR_FUTURE = "9999-12-31T00:00:00"


# ==================================================================
# الحماية بالتوكن
# ==================================================================
def expected_token() -> str:
    """سرّ الواجهة من بيئة المساحة (فارغ = المسارات مغلقة)."""
    return os.getenv("ADMIN_API_TOKEN", "").strip()


def token_ok(got: str, expected: str) -> bool:
    """مقارنة زمنية ثابتة أن التوكن المُرسل مطابق للسرّ."""
    import hmac

    got, expected = (got or "").strip(), (expected or "").strip()
    if not got or not expected:
        return False
    return hmac.compare_digest(got, expected)


def _auth(request):
    """Authenticate the request and enforce the server-side token role."""
    expected = expected_token()
    configured: list[dict[str, Any]] = []
    raw = os.getenv("ADMIN_API_TOKENS_JSON", "").strip()
    if raw:
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
    if expected:
        configured.append({"token": expected, "role": "owner", "admin_id": 0})
    if not configured:
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
    principal = next(
        (item for item in configured if token_ok(got, item["token"])), None
    )
    if principal is None:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    method = getattr(request, "method", "POST").upper()
    if path.startswith("/api/admin/export/") or path.startswith("/api/admin/backup/"):
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

    permissions = {
        "viewer": {"read"},
        "reviewer": {"read", "review"},
        "operator": {"read", "review", "users", "operate", "export"},
        "owner": {"read", "review", "users", "operate", "export", "delete"},
    }
    if permission not in permissions[principal["role"]]:
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    try:
        request.state.admin_role = principal["role"]
        request.state.admin_id = principal["admin_id"]
    except AttributeError:
        pass
    return None


def _admin_actor(request) -> int:
    return int(getattr(getattr(request, "state", None), "admin_id", 0) or 0)


# ==================================================================
# إشعار المتبلّغ (نفس آلية التكرار التي تستخدمها لوحة تيليغرام)
# ==================================================================
def _bot_runtime():
    """(bot, loop) إذا كان البوت حيًّا، أو (None, None)."""
    import main as bot_main  # noqa: PLC0415 — ضمن الدالة لتجنب الدوران

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


async def _send_direct_message(user_id: int, text: str) -> bool:
    async def _sender():
        bot, loop = _bot_runtime()
        if bot is None:
            return False
        try:
            await bot.send_message(
                chat_id=int(user_id),
                text=f"📨 رسالة من الجهة المختصة:\n\n{text}",
            )
            return True
        except Exception:
            logger.info("تعذر إرسال رسالة مباشرة للمستخدم", exc_info=True)
            return False

    return await _on_bot_loop(_sender)


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
    import admin as admin_mod  # noqa: PLC0415

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
        import main as bot_main  # noqa: PLC0415

        return bot_main.app.bot_data["db"]

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
                import config  # noqa: PLC0415
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
            if size < 1:
                return bad("bad_query")
        except ValueError:
            return bad("bad_query")
        items, total = await db_of().list_blocked_users(page=page, size=size)
        return JSONResponse({
            "ok": True, "page": page, "size": size, "total": total, "items": items,
        })

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
        sent = await _send_direct_message(uid, text)
        if not sent:
            await db.audit(
                _admin_actor(request), "user_message_failed", str(uid), text[:120]
            )
            return bad("telegram_send_failed", 502)
        await db.open_user_conversation(uid)
        await db.add_user_message(uid, "out", text, _admin_actor(request))
        await db.audit(
            _admin_actor(request), "user_message", str(uid), f"sent: {text[:120]}"
        )
        return JSONResponse({"ok": True, "sent": True})

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
            "items": await db.list_user_messages(uid, after_id, limit),
        })

    # ------------------------------------------------------------
    # 4) سجل التدقيق + التصدير (لتكميل أروقة الالتزام)
    # ------------------------------------------------------------
    @app.get("/api/admin/audit")
    async def audit_api(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        limit = min(int(request.query_params.get("limit", "50") or 50), 200)
        return JSONResponse({"ok": True, "items": await db_of().recent_audit(limit)})

    @app.get("/api/admin/meta")
    async def meta(request: Request) -> JSONResponse:
        """عناصر ثابتة تحتاجها الواجهات: قائمة التصنيفات المعتمدة."""
        refuse = _auth(request)
        if refuse:
            return refuse
        import config  # noqa: PLC0415

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
        if local is not None and local.is_file():
            media_type = mimetypes.guess_type(local.name)[0] or "application/octet-stream"
            if not media_type.startswith("image/"):
                media_type = "application/octet-stream"
            return FileResponse(
                str(local),
                media_type=media_type,
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
        return Response(
            content=data,
            media_type=media_type,
            headers={"Cache-Control": "private, no-store"},
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
        import config  # noqa: PLC0415
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
        import config  # noqa: PLC0415
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
                    try:
                        await bot.send_message(
                            chat_id=uid, text=payload, parse_mode=_parse_html()
                        )
                        sent += 1
                    except Exception:
                        failed += 1
                        logger.debug("Broadcast delivery failed for one recipient")
                    if index % 20 == 0:
                        await db.update_broadcast_job(job_id, sent, failed)
                    await asyncio.sleep(0.08)
                await db.update_broadcast_job(
                    job_id, sent, failed, status="completed"
                )
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

    @app.get("/api/admin/broadcast/{job_id}")
    async def broadcast_status(job_id: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        job = await db_of().get_broadcast_job(job_id)
        if not job:
            return bad("not_found", 404)
        return JSONResponse({"ok": True, **job})

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
        import config  # noqa: PLC0415

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

    @app.put("/api/admin/settings")
    async def runtime_settings_put(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        import config  # noqa: PLC0415

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
        import hub_backup  # noqa: PLC0415

        return JSONResponse({
            "ok": True,
            "bot_ready": await _bot_ready(),
            "database_ready": database_ready,
            "backend": db.backend,
            "database_error": database_error,
            "reports_total": total,
            "backup_configured": hub_backup.is_configured(),
            "emergency": await db.get_state("emergency") == "1",
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
        db = db_of()
        import hub_backup  # noqa: PLC0415

        if db.backend == "turso":
            import tempfile  # noqa: PLC0415
            from pathlib import Path  # noqa: PLC0415

            fd, snapshot_path = tempfile.mkstemp(prefix="mrw-backup-upload-", suffix=".db")
            os.close(fd)
            snapshot = Path(snapshot_path)
            try:
                await asyncio.to_thread(db.export_sqlite_snapshot, snapshot)
                ok = await asyncio.to_thread(hub_backup.upload_backup, snapshot)
            except Exception:
                logger.exception("تعذر تجهيز نسخة Turso للرفع")
                ok = False
            finally:
                snapshot.unlink(missing_ok=True)
        else:
            ok = await asyncio.to_thread(hub_backup.upload_backup, db.path)
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
        path = Path(__file__).resolve().parent / path
    return path


async def _json(request) -> dict[str, Any]:
    """يقرأ جسم الطلب JSON بأمان (فارغ عند الخطأ)."""
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}
