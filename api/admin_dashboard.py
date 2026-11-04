from __future__ import annotations

import importlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from api import admin_api

logger = logging.getLogger(__name__)

#: الواجهة الإدارية الوحيدة هي لوحة Flutter المخدومة على هذا المسار.
ADMIN_UI_PATH = "/admin/"


async def build_dashboard_payload(db: Any) -> dict[str, Any]:
    """Assemble a summary payload for dashboard API clients."""
    counts = await db.count_by_status()
    now = datetime.now(timezone.utc)
    last24 = await db.count_since(
        (now - timedelta(hours=24)).isoformat(timespec="seconds")
    )
    workflow = await db.workflow_counts()
    total_users = await db.total_users()
    total_reports = await db.total()
    blocked_users = await db.blocked_users_count()

    return {
        "ok": True,
        "generated_at": now.isoformat(timespec="seconds"),
        "summary": {
            "total_reports": total_reports,
            "last_24_hours": last24,
            "total_users": total_users,
            "blocked_users": blocked_users,
            "new_reports": counts.get("جديد", 0),
            "in_review": counts.get("قيد المراجعة", 0),
            "approved": counts.get("معتمد", 0),
            "rejected": counts.get("مرفوض", 0),
        },
        "workflow": {
            "urgent": workflow.get("urgent", 0),
            "overdue": workflow.get("overdue", 0),
            "unassigned": workflow.get("unassigned", 0),
        },
        "status_breakdown": [
            {"label": "جديد", "value": counts.get("جديد", 0), "tone": "warning"},
            {
                "label": "قيد المراجعة",
                "value": counts.get("قيد المراجعة", 0),
                "tone": "info",
            },
            {"label": "معتمد", "value": counts.get("معتمد", 0), "tone": "success"},
            {"label": "مرفوض", "value": counts.get("مرفوض", 0), "tone": "danger"},
        ],
    }


def register_dashboard(app: Any) -> None:
    """Register the dashboard compatibility routes.

    لوحة Flutter على ``/admin/`` هي واجهة الإدارة الوحيدة. يبقى ``/dashboard``
    تحويلاً دائماً كي لا تتعطل الروابط القديمة، ويبقى ``/api/admin/dashboard``
    عقداً برمجياً لمن يستهلك الملخّص من خارج الواجهة.
    """

    @app.get("/dashboard", include_in_schema=False)
    async def dashboard_page() -> RedirectResponse:
        return RedirectResponse(ADMIN_UI_PATH, status_code=307)

    @app.get("/api/admin/dashboard")
    async def dashboard_api(request: Request) -> JSONResponse:
        refuse = admin_api._auth(request)
        if refuse:
            return refuse
        try:
            bot_main = importlib.import_module("app.main")
            db = bot_main.app.bot_data.get("db") if bot_main.app else None
            if db is None:
                return JSONResponse(
                    {"ok": False, "error": "bot_not_ready"}, status_code=503
                )
            return JSONResponse(await build_dashboard_payload(db))
        except Exception:
            logger.exception("Failed to build admin dashboard payload")
            return JSONResponse(
                {"ok": False, "error": "dashboard_unavailable"}, status_code=500
            )
