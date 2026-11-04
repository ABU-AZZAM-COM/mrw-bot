"""مسارات البثّ الجماعي والمنشورات: المعاينة، الإرسال، والمتابعة.

انتقلت من ``api/admin_api.py``. تحفظ نفس تتبّع المهام (jobs) والتحقق من
المحتوى قبل الإرسال.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from api.admin_auth import _admin_actor, _auth, _owner_only
from bot import texts
from services import utils


def _late_bound(name: str):
    """غلاف يحلّ الاسم من ``admin_api`` وقت الطلب لا وقت التركيب."""

    def call(*args, **kwargs):
        from api import admin_api as core

        return getattr(core, name)(*args, **kwargs)

    return call


def register(app, *, db_of, bad) -> None:
    logger = logging.getLogger(__name__)

    from api import admin_api as core

    MAX_TEXT = core.MAX_TEXT
    PUBLICATION_CATEGORY_LABELS = core.PUBLICATION_CATEGORY_LABELS

    _bot_runtime = _late_bound("_bot_runtime")
    _image_media_type = _late_bound("_image_media_type")
    _json = _late_bound("_json")
    _on_bot_loop_value = _late_bound("_on_bot_loop_value")
    _parse_html = _late_bound("_parse_html")
    _publication_delivery_text = _late_bound("_publication_delivery_text")
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

