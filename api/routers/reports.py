"""مسارات البلاغات الإدارية: القائمة، التفاصيل، سير العمل، والملاحظات.

انتقلت من ``api/admin_api.py``. تستقبل المساعدات المشتركة صراحةً من
``admin_api.register`` حتى لا تعيد تعريف المصادقة أو الوصول لقاعدة البيانات.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Request
from fastapi.responses import JSONResponse

from database import (
    STATUS_APPROVED,
    STATUS_AWAITING_INFO,
    STATUS_CLOSED_NO_RESPONSE,
    STATUS_NEW,
    STATUS_REJECTED,
    STATUS_REVIEW,
)


def register(
    app,
    *,
    db_of,
    bad,
    auth,
    json_body,
    safe_report,
    admin_actor,
    notify_status,
    send_panel_reply,
    ok_response,
) -> None:
    from api import admin_api as core

    MAX_PAGE_SIZE = core.MAX_PAGE_SIZE
    DEFAULT_PAGE_SIZE = core.DEFAULT_PAGE_SIZE
    MAX_TEXT = core.MAX_TEXT

    @app.get("/api/admin/reports")
    async def reports(request: Request) -> JSONResponse:
        refuse = auth(request)
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
            "items": [safe_report(r) for r in rows],
        })

    @app.get("/api/admin/reports/{rid}")
    async def report_detail(rid: int, request: Request) -> JSONResponse:
        refuse = auth(request)
        if refuse:
            return refuse
        r = await db_of().get_by_id(rid)
        if not r:
            return bad("not_found", 404)
        item = safe_report(r)
        item["appeals"] = await db_of().list_report_appeals(rid)
        return JSONResponse({"ok": True, "item": item})

    @app.put("/api/admin/reports/{rid}/workflow")
    async def report_workflow(rid: int, request: Request) -> JSONResponse:
        refuse = auth(request)
        if refuse:
            return refuse
        body = await json_body(request)
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
            admin_actor(request), "report_workflow_changed", str(rid),
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
        refuse = auth(request)
        if refuse:
            return refuse
        body = await json_body(request)
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
        from app import config  # noqa: PLC0415

        due_at = datetime.now(timezone.utc) + timedelta(days=2)
        local_due = due_at.astimezone(
            timezone(timedelta(hours=config.LOCAL_UTC_OFFSET))
        )
        due_iso = due_at.isoformat(timespec="seconds")
        sent = await _send_direct_message(
            int(report["user_id"]),
            f"نحتاج إلى معلومات إضافية بخصوص البلاغ {report.get('seq') or rid}:\n\n"
            f"{note}\n\n"
            f"أرسل ردك قبل {local_due.strftime('%Y-%m-%d %H:%M')} "
            f"(التوقيت المحلي UTC{config.LOCAL_UTC_OFFSET:+g}). "
            "سيُغلق البلاغ تلقائيًا بعد هذا الموعد إذا لم يصل رد.",
            reply_markup=keyboards.report_followup_reply_kb(rid),
        )
        if not sent:
            return bad("telegram_send_failed", 502)
        await db.set_followup_request(rid, note, due_iso)
        await db.audit(
            admin_actor(request), "report_followup_requested", str(rid), note[:MAX_TEXT]
        )
        return JSONResponse({"ok": True, "sent": True})

    # ------------------------------------------------------------
    # 2) إجراءات البلاغ: الحالة/التصنيف/الملاحظة/الرد/الحذف
    # ------------------------------------------------------------
    @app.post("/api/admin/reports/{rid}/status")
    async def set_status(rid: int, request: Request) -> JSONResponse:
        refuse = auth(request)
        if refuse:
            return refuse
        body = await json_body(request)
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

        ok = await db.update_status(rid, new_status, admin_actor(request))
        if not ok:
            return bad("not_found", 404)
        audit_details = f"{new_status} (لوحة الإدارة)"
        if new_status == STATUS_REJECTED:
            audit_details += f" — سبب الرفض: {reason}"
        await db.audit(admin_actor(request), "status_changed", str(rid),
                       audit_details)

        # الإشعار: الرفض بسبب = دائمًا؛ غيره = مرة واحدة لكل حالة (نفس دفتر تيليغرام)
        note_extra = ""
        if new_status == STATUS_REJECTED and reason:
            note_extra = texts.REJECT_REASON_NOTIF.format(reason=utils.esc(reason))
        notified = await notify_status(await db.get_by_id(rid), new_status, note_extra)
        return JSONResponse({"ok": True, "notified": notified})

    @app.post("/api/admin/reports/{rid}/type")
    async def set_type(rid: int, request: Request) -> JSONResponse:
        refuse = auth(request)
        if refuse:
            return refuse
        body = await json_body(request)
        vtype = (str(body.get("violation_type") or "").strip()) or None
        if vtype and len(vtype) > 60:
            return bad("type_too_long")
        ok = await db_of().set_violation_type(rid, vtype)
        if ok:
            await db_of().audit(
                admin_actor(request), "type_changed", str(rid), vtype or "(فارغ)"
            )
        return ok_response if ok else bad("not_found", 404)

    @app.post("/api/admin/reports/{rid}/note")
    async def set_note(rid: int, request: Request) -> JSONResponse:
        refuse = auth(request)
        if refuse:
            return refuse
        body = await json_body(request)
        note = (str(body.get("note") or "").strip())[:MAX_TEXT] or None
        ok = await db_of().set_admin_note(rid, note)
        if ok:
            await db_of().audit(
                admin_actor(request), "note_changed", str(rid), note or "(فارغ)"
            )
        return ok_response if ok else bad("not_found", 404)

    @app.post("/api/admin/reports/{rid}/reply")
    async def reply_reporter(rid: int, request: Request) -> JSONResponse:
        refuse = auth(request)
        if refuse:
            return refuse
        body = await json_body(request)
        text = (str(body.get("text") or "").strip())
        if not 2 <= len(text) <= MAX_TEXT:
            return bad("text_length")
        db = db_of()
        r = await db.get_by_id(rid)
        if not r:
            return bad("not_found", 404)
        sent = await send_panel_reply(r, text)
        await db.audit(admin_actor(request), "panel_reply", str(rid),
                       text[:120] + ("…" if len(text) > 120 else ""))
        if not sent:
            return bad("bot_not_ready", 503)
        return JSONResponse({"ok": True})

    @app.delete("/api/admin/reports/{rid}")
    async def delete_report(rid: int, request: Request) -> JSONResponse:
        refuse = auth(request)
        if refuse:
            return refuse
        body = await json_body(request)
        db = db_of()
        r = await db.get_by_id(rid)
        if not r:
            return bad("not_found", 404)
        if str(body.get("confirm_seq") or "").strip() != str(r.get("seq") or ""):
            return bad("must_confirm_seq")
        ok = await db.delete_report(rid)
        if ok:
            await db.audit(admin_actor(request), "report_deleted", str(r.get("seq") or str(rid)),
                           f"المستخدم {r.get('user_id')}")
        return ok_response if ok else bad("not_found", 404)

