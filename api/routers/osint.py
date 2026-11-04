"""مسارات مصادر OSINT: القراءة والإضافة والحذف.

انتقلت من ``api/admin_api.py`` مع نفس التحقق من الروابط العامة.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from api.admin_auth import _admin_actor, _auth


def register(app, *, db_of, bad) -> None:
    from api import admin_api as core

    def _json(*args, **kwargs):
        return core._json(*args, **kwargs)

    def _sanitize_public_source_url(*args, **kwargs):
        return core._sanitize_public_source_url(*args, **kwargs)
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
