"""
واجهة التحكم الإدارية (Admin API) — نقاط HTTPS محمية تخوّل المشرف تحكمًا كاملًا.

تُستدعى من:
  • تطبيق سطح المكتب (PySide6) الذي أعددناه لجهاز المشرف، و
  • لوحة الويب المنشورة على نفس المساحة، و
  • أي أداة تشغيلية مستقبلية (أوامر آلية، تقارير مجدولة…).

الحماية:
  1) سرّ ADMIN_API_TOKEN في أسرار المساحة — إن غاب فالمسار كله مغلق (503).
  2) كل طلب يحمَل الرأس  X-Admin-Token  أو  ?token=  ويقارَن بمقارنة زمنية ثابتة.
  3) لا يجدُد للعلن: كل عملية تُسجَّل في audit_log (من فعل ماذا ومتى).

مبدأ مصمَّم ببساطة: أي عملية تلمس قاعدة البيانات تمر عبر Database (نفس طبقة
البوت أخذًا للأقفال والتدقيق)، وأي إرسال لتلغرام يُنفَّذ على حلقة البوت نفسها
(cross-thread عبر run_coroutine_threadsafe) حتى لا تختلط حلقات uvicorn/PTB.

كل النقاط JSON وتعيد {"ok": true/false, ...} باستثناء مسار التصدير (ملف).
"""
from __future__ import annotations

import asyncio
import logging
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
    """يعيد JSONResponse للرفض، أو None للموافقة (يُستدعى من كل نقطة)."""
    expected = expected_token()
    if not expected:
        return JSONResponse(
            {"ok": False, "error": "api_token_not_configured"}, status_code=503
        )
    got = (
        request.headers.get("x-admin-token")
        or request.query_params.get("token")
        or ""
    )
    if not token_ok(got, expected):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    return None


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


async def _send_block_notice(user_id: int, kind: str, reason: str) -> bool:
    async def _sender():
        bot, loop = _bot_runtime()
        if bot is None:
            return False
        try:
            await bot.send_message(
                chat_id=int(user_id),
                text=texts.PANEL_BLOCK_NOTIF.format(
                    kind=utils.esc(kind), reason=utils.esc(reason)
                ),
                parse_mode=_parse_html(),
            )
            return True
        except Exception:
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

    def db_of() -> Any:  # noqa: ANN202
        import main as bot_main  # noqa: PLC0415

        return bot_main.app.bot_data["db"]

    # ------------------------------------------------------------
    # 0) نظرة عامة
    # ------------------------------------------------------------
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
            "bot_ready": await _bot_ready(),
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
            rows, total = await db.list_reports_filtered(
                status=qp.get("status") or None,
                vtype=qp.get("type") or None,
                user_id=int(qp["user_id"]) if qp.get("user_id", "").strip() else None,
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

        r = await db.get_by_id(rid)
        if not r:
            return bad("not_found", 404)

        ok = await db.update_status(rid, new_status, 0)  # 0 = لوحة الإدارة
        if not ok:
            return bad("not_found", 404)
        await db.audit(0, "status_changed", str(rid),
                       f"{new_status} (لوحة الإدارة)")

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
            await db_of().audit(0, "type_changed", str(rid), vtype or "(فارغ)")
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
            await db_of().audit(0, "note_changed", str(rid), note or "(فارغ)")
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
        await db.audit(0, "panel_reply", str(rid),
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
            await db.audit(0, "report_deleted", str(r.get("seq") or str(rid)),
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
        await db.audit(0, "user_blocked", str(uid), f"{kind} — {reason}")
        await _send_block_notice(uid, kind, reason)
        return JSONResponse({"ok": True, "frozen_until": until})

    @app.post("/api/admin/users/{uid}/unblock")
    async def unblock_user(uid: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        db = db_of()
        await db.clear_block(uid)
        await db.audit(0, "user_unblocked", str(uid), "رفع الحجب من لوحة الإدارة")
        return OK

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

        local = P(str(r.get("photo_path") or ""))
        if local.exists():
            return FileResponse(str(local), media_type="image/jpeg")
        file_id = r.get("photo_file_id")
        if not file_id:
            return bad("no_photo", 404)

        data = await _photo_bytes(file_id)
        if not data:
            return bad("photo_unavailable", 503)
        from fastapi.responses import Response  # noqa: PLC0415

        return Response(content=data, media_type="image/jpeg")

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
        await db.audit(0, "panel_export", fmt, f"عدد البلاغات: {len(rows)}")
        return FileResponse(str(path), filename=path.name)


# ==================================================================
# أدوات مساعدة
# ==================================================================
def _safe_report(r: dict[str, Any]) -> dict[str, Any]:
    """نسخة صالحة للعرض عبر الإنترنت (الصورة تُسحب من تلغرام عبر file_id)."""
    return {k: v for k, v in r.items() if k != "photo_path"}


async def _json(request) -> dict[str, Any]:
    """يقرأ جسم الطلب JSON بأمان (فارغ عند الخطأ)."""
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}
