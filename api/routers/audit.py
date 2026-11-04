"""مسارات سجل التدقيق: الاستعلام، التصدير، والتنظيف.

انتقلت من ``api/admin_api.py``. التنظيف محصور بالمالك، والتصدير يتطلب
صلاحية ``export`` التي يفرضها ``_auth`` مركزيًا.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from api.admin_auth import _admin_actor, _auth, _owner_only


def register(app, *, db_of, bad) -> None:
    from api import admin_api as core

    def _json(*args, **kwargs):
        return core._json(*args, **kwargs)

    def _audit_date_range(*args, **kwargs):
        return core._audit_date_range(*args, **kwargs)
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

