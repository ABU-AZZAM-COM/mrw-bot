"""مسارات الإشعارات وبثّ الأحداث الإدارية.

انتقلت من ``api/admin_api.py`` كأول قسم مستقل بعد المصادقة. تعتمد على
المساعدات المشتركة التي يمرّرها ``admin_api.register`` عند التركيب، فلا
تعيد تعريف المصادقة أو الوصول إلى قاعدة البيانات، ولا تنشأ استيرادات دائرية.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse


def register(
    app,
    *,
    db_of,
    bad,
    auth,
    owner_only,
    admin_actor,
) -> None:
    """تركيب مسارات الإشعارات على التطبيق."""

    @app.get("/api/admin/events/stream")
    async def events_stream(request: Request):
        refuse = auth(request)
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
        refuse = auth(request)
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
        refuse = auth(request)
        if refuse:
            return refuse
        return JSONResponse({"ok": True, **await db_of().notification_stats()})

    @app.post("/api/admin/notifications/cleanup")
    async def cleanup_notifications(request: Request):
        refuse = auth(request)
        if refuse:
            return refuse
        refuse = owner_only(request)
        if refuse:
            return refuse
        db = db_of()
        deleted = await db.cleanup_notifications()
        await db.audit(
            admin_actor(request),
            "notifications_cleanup",
            "notifications",
            f"deleted={deleted}",
        )
        return JSONResponse({"ok": True, "deleted": deleted})

    @app.post("/api/admin/notifications/clear")
    async def clear_notifications(request: Request):
        refuse = auth(request)
        if refuse:
            return refuse
        refuse = owner_only(request)
        if refuse:
            return refuse
        db = db_of()
        deleted = await db.clear_notifications()
        await db.audit(
            admin_actor(request),
            "notifications_clear",
            "notifications",
            f"deleted={deleted}",
        )
        return JSONResponse({"ok": True, "deleted": deleted})

    @app.post("/api/admin/notifications/{notification_id}/read")
    async def read_notification(notification_id: int, request: Request):
        refuse = auth(request)
        if refuse:
            return refuse
        changed = await db_of().mark_notification_read(notification_id)
        if not changed:
            return bad("notification_not_found", 404)
        return JSONResponse({"ok": True})

    @app.post("/api/admin/notifications/read-all")
    async def read_all_notifications(request: Request):
        refuse = auth(request)
        if refuse:
            return refuse
        count = await db_of().mark_all_notifications_read()
        return JSONResponse({"ok": True, "updated": count})
