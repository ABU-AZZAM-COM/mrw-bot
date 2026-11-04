"""مسارات المنصّة والتشغيل: الإصدار، الصحة، الإحصاءات، النسخ الاحتياطي، والتصدير.

انتقلت من ``api/admin_api.py``. تشمل أيضاً النظرة العامة والتصنيفات والقوالب
والصور وإعدادات المحتوى. الصلاحيات يفرضها ``_auth`` مركزيًا حسب المسار.
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response

from api.admin_auth import _admin_actor, _auth, _owner_only
from database import (
    STATUS_APPROVED,
    STATUS_AWAITING_INFO,
    STATUS_CLOSED_NO_RESPONSE,
    STATUS_NEW,
    STATUS_REJECTED,
    STATUS_REVIEW,
)
from services.report_taxonomy import REPORT_CATEGORIES


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
    DEFAULT_PAGE_SIZE = core.DEFAULT_PAGE_SIZE
    MAX_PAGE_SIZE = core.MAX_PAGE_SIZE

    _bot_ready = _late_bound("_bot_ready")
    _image_media_type = _late_bound("_image_media_type")
    _json = _late_bound("_json")
    _local_photo_path = _late_bound("_local_photo_path")
    _main_module = _late_bound("_main_module")
    _photo_bytes = _late_bound("_photo_bytes")
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

    # ================================================================
    # endpoints إدارة النصوص
    # ================================================================


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


