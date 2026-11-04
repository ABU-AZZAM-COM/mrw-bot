"""مسارات المستخدمين والمحادثات والرسائل المباشرة.

انتقلت من ``api/admin_api.py``. تشمل الملف التعريفي، الحجب، الرسائل
والمرفقات، وإعدادات التواصل. الصلاحيات يفرضها ``_auth`` مركزيًا حسب المسار.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response

from api.admin_auth import FAR_FUTURE, _admin_actor, _auth, _owner_only
from services import chat_attachments, utils


def _late_bound(name: str):
    """غلاف يحلّ الاسم من ``admin_api`` وقت الطلب لا وقت التركيب.

    يبقي الدوال قابلة للاستبدال في الاختبارات عبر ``patch`` على
    ``api.admin_api``، ويمنع الاستيراد الدائري.
    """

    def call(*args, **kwargs):
        from api import admin_api as core

        return getattr(core, name)(*args, **kwargs)

    return call


def register(app, *, db_of, bad) -> None:
    logger = logging.getLogger(__name__)

    from api import admin_api as core

    DEFAULT_PAGE_SIZE = core.DEFAULT_PAGE_SIZE
    MAX_PAGE_SIZE = core.MAX_PAGE_SIZE
    MAX_TEXT = core.MAX_TEXT

    _bot_runtime = _late_bound("_bot_runtime")
    _image_media_type = _late_bound("_image_media_type")
    _json = _late_bound("_json")
    _local_photo_path = _late_bound("_local_photo_path")
    _on_bot_loop_value = _late_bound("_on_bot_loop_value")
    _photo_bytes = _late_bound("_photo_bytes")
    _safe_report = _late_bound("_safe_report")
    _send_block_notice = _late_bound("_send_block_notice")
    _send_direct_message = _late_bound("_send_direct_message")
    _send_direct_message_result = _late_bound("_send_direct_message_result")
    _send_unblock_notice = _late_bound("_send_unblock_notice")
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
        await db.open_user_conversation(uid)
        message_id = await db.add_user_message(
            uid,
            "out",
            text,
            _admin_actor(request),
            delivery_status="delivered",
        )
        preferences = await db.user_contact_preferences(uid)
        alert_sent = False
        if preferences["telegram_reply_alerts"]:
            alert_sent = await _send_direct_message(
                uid,
                "لديك رد جديد من الإدارة في محادثة التواصل. افتح التطبيق لقراءته.",
            )
        await db.audit(
            _admin_actor(request),
            "user_message",
            str(uid),
            f"message_id={message_id}; telegram_alert="
            f"{'sent' if alert_sent else 'disabled_or_failed'}",
        )
        return JSONResponse({
            "ok": True,
            "sent": True,
            "message_id": message_id,
            "telegram_alert_sent": alert_sent,
        })

    @app.post("/api/admin/users/{uid}/media")
    async def message_user_media(uid: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        db = db_of()
        if not await db.user_profile(uid):
            return bad("not_found", 404)
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > chat_attachments.MAX_CHAT_ATTACHMENT_BYTES + 512 * 1024:
                    return bad("media_too_large", 413)
            except ValueError:
                return bad("bad_content_length")
        try:
            form = await request.form()
        except Exception:
            logger.exception("تعذر قراءة وسيط الرسالة المباشرة")
            return bad("multipart_unavailable", 503)
        caption = str(form.get("caption") or "").strip()
        upload = form.get("media")
        if len(caption) > 1000:
            return bad("caption_too_long")
        if not hasattr(upload, "read"):
            return bad("media_required")
        filename = chat_attachments.sanitize_attachment_filename(
            getattr(upload, "filename", None)
        )
        try:
            media_data = await upload.read(
                chat_attachments.MAX_CHAT_ATTACHMENT_BYTES + 1
            )
        finally:
            await upload.close()
        if len(media_data) > chat_attachments.MAX_CHAT_ATTACHMENT_BYTES:
            return bad("media_too_large", 413)
        detected = chat_attachments.validate_chat_attachment(media_data, filename)
        if detected is None:
            return bad("unsupported_attachment", 415)
        detected_kind, content_type = detected
        media_type = "photo" if detected_kind == "image" else "document"
        await db.open_user_conversation(uid)
        message_id = await db.add_user_message(
            uid,
            "out",
            caption or f"[مرفق: {filename}]",
            _admin_actor(request),
            media_type=media_type,
            filename=filename,
            attachment_data=media_data,
            attachment_content_type=content_type,
        )
        preferences = await db.user_contact_preferences(uid)
        alert_sent = False
        if preferences["telegram_reply_alerts"]:
            alert_sent = await _send_direct_message(
                uid,
                "لديك مرفق جديد من الإدارة في محادثة التواصل. افتح التطبيق لعرضه.",
            )
        await db.audit(
            _admin_actor(request),
            "user_media_message",
            str(uid),
            f"message_id={message_id}; type={media_type}; "
            f"size={len(media_data)}; telegram_alert="
            f"{'sent' if alert_sent else 'disabled_or_failed'}",
        )
        return JSONResponse({
            "ok": True,
            "sent": True,
            "message_id": message_id,
            "telegram_alert_sent": alert_sent,
        })

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
        global_raw = await db_of().get_state("contact_global_enabled")
        return JSONResponse({
            "ok": True,
            "items": items,
            "contact_global_enabled": global_raw != "0",
        })

    @app.get("/api/admin/contact-settings")
    async def get_contact_settings(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        raw = await db_of().get_state("contact_global_enabled")
        return JSONResponse({"ok": True, "enabled": raw != "0"})

    @app.put("/api/admin/contact-settings")
    async def update_contact_settings(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        body = await _json(request)
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            return bad("bad_contact_setting")
        db = db_of()
        await db.set_state("contact_global_enabled", "1" if enabled else "0")
        await db.audit(
            _admin_actor(request),
            "contact_global_enabled",
            "all_users",
            "enabled" if enabled else "disabled",
        )
        return JSONResponse({"ok": True, "enabled": enabled})

    @app.delete("/api/admin/conversations/{uid}")
    async def delete_user_conversation(uid: int, request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        body = await _json(request)
        reason = str(body.get("reason") or "").strip()
        if not 5 <= len(reason) <= 500:
            return bad("deletion_reason_required")
        deleted = await db_of().delete_user_conversation(
            uid,
            _admin_actor(request),
            reason,
        )
        if not deleted:
            return bad("conversation_not_found", 404)
        return JSONResponse({"ok": True})

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
        if "after_id" in request.query_params:
            items = await db.list_user_messages(
                uid, after_id, limit, conversation_only=True
            )
        else:
            items = await db.list_user_messages_before(
                uid, 0, limit, conversation_only=True
            )
        return JSONResponse({
            "ok": True,
            "can_manage_messages": (
                getattr(request.state, "admin_role", "") == "owner"
            ),
            "can_delete_conversation": "contact_users" in getattr(
                request.state, "admin_permissions", []
            ),
            "items": [
                {
                    key: value
                    for key, value in item.items()
                    if key != "telegram_file_id"
                }
                | {
                    "has_media": bool(
                        item.get("telegram_file_id") or item.get("has_attachment")
                    )
                }
                for item in items
            ],
        })

    @app.get("/api/admin/users/{uid}/messages/{message_id}/media")
    async def get_user_message_media(
        uid: int, message_id: int, request: Request
    ) -> Response:
        refuse = _auth(request)
        if refuse:
            return refuse
        messages = await db_of().list_user_messages(
            uid,
            max(0, message_id - 1),
            1,
            conversation_only=True,
        )
        if not messages or int(messages[0]["id"]) != message_id:
            return bad("not_found", 404)
        item = messages[0]
        if item.get("is_deleted"):
            return bad("media_not_available", 404)
        attachment = await db_of().get_user_message_attachment(uid, message_id)
        if attachment is not None:
            return Response(
                content=attachment["data"],
                media_type=attachment["content_type"],
                headers={
                    "Cache-Control": "private, no-store",
                    "Content-Disposition": (
                        "attachment; filename*=UTF-8''"
                        + urllib.parse.quote(attachment["filename"])
                    ),
                    "X-Content-Type-Options": "nosniff",
                    "X-Content-Type-Options": "nosniff",
                },
            )
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
        if media_type == "photo":
            filename = "attachment.jpg"
        detected = chat_attachments.validate_chat_attachment(bytes(data), filename)
        if detected is None:
            return bad("unsupported_attachment", 415)
        _media_kind, content_type = detected
        return Response(
            content=bytes(data),
            media_type=content_type,
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": (
                    "attachment; filename*=UTF-8''"
                    + urllib.parse.quote(filename or f"message-{message_id}")
                ),
                "X-Content-Type-Options": "nosniff",
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


