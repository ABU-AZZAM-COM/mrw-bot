from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

from api import admin_api

logger = logging.getLogger(__name__)


async def build_dashboard_payload(db: Any) -> dict[str, Any]:
    """Assemble a polished summary payload for the modern admin dashboard."""
    counts = await db.count_by_status()
    now = datetime.now(timezone.utc)
    last24 = await db.count_since((now - timedelta(hours=24)).isoformat(timespec="seconds"))
    workflow = await db.workflow_counts()
    total_users = await db.total_users()
    total_reports = await db.total()
    blocked_users = await db.blocked_users_count()

    payload = {
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
            {"label": "قيد المراجعة", "value": counts.get("قيد المراجعة", 0), "tone": "info"},
            {"label": "معتمد", "value": counts.get("معتمد", 0), "tone": "success"},
            {"label": "مرفوض", "value": counts.get("مرفوض", 0), "tone": "danger"},
        ],
    }
    return payload


def render_dashboard_html(summary: dict[str, Any]) -> str:
    """Render a richer HTML dashboard that loads live data and shows a small report table."""
    stats = summary.get("summary", {})
    workflow = summary.get("workflow", {})
    breakdown = summary.get("status_breakdown", [
        {"label": "جديد", "value": 0, "tone": "warning"},
        {"label": "قيد المراجعة", "value": 0, "tone": "info"},
        {"label": "معتمد", "value": 0, "tone": "success"},
        {"label": "مرفوض", "value": 0, "tone": "danger"},
    ])

    cards = "".join(
        """
        <div class=\"card\">
          <div class=\"label\">{label}</div>
          <div class=\"value\" id=\"status-{tone}\">{value}</div>
        </div>
        """.format(
            label=item.get("label", ""),
            value=item.get("value", 0),
            tone=item.get("tone", "info"),
        )
        for item in breakdown
    )

    html = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>لوحة التحكم</title>
    <style>
        :root {
            --bg: #0b1220;
            --panel: #111c2d;
            --card: #12223b;
            --card-2: #16243c;
            --line: rgba(255,255,255,0.08);
            --text: #e5eefc;
            --muted: #9fb3d1;
            --warning: #fbbf24;
            --danger: #f87171;
            --success: #34d399;
            --info: #7dd3fc;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0; font-family: 'Segoe UI', Tahoma, sans-serif;
            background: linear-gradient(180deg, #0b1220, #0d1a2b 38%, #101b2d);
            color: var(--text);
        }
        .shell { max-width: 1280px; margin: 0 auto; padding: 22px 18px 64px; }
        .topbar {
            display: flex; justify-content: space-between; align-items: center; gap: 12px;
            background: rgba(17, 28, 45, 0.84); border: 1px solid var(--line);
            border-radius: 18px; padding: 18px 20px; margin-bottom: 22px;
        }
        .brand { font-size: 1.2rem; font-weight: 700; }
        .meta { color: var(--muted); font-size: 0.8rem; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 22px; }
        .card {
            background: linear-gradient(180deg, var(--card), var(--card-2)); border: 1px solid var(--line);
            border-radius: 18px; padding: 20px; box-shadow: 0 18px 36px rgba(0,0,0,0.12);
        }
        .label { color: var(--muted); font-size: 0.8rem; }
        .value { font-size: 2rem; font-weight: 800; margin-top: 12px; }
        .section {
            background: rgba(17, 28, 45, 0.88); border: 1px solid var(--line); border-radius: 18px;
            padding: 18px; margin-top: 20px;
        }
        .section h3 { margin: 0 0 14px; font-size: 1.05rem; }
        .stats-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 14px; }
        .pill {
            background: rgba(255,255,255,0.02); border: 1px solid var(--line); border-radius: 12px;
            padding: 12px 14px;
        }
        .pill .small { color: var(--muted); font-size: 0.77rem; }
        .pill strong { display: block; font-size: 1.4rem; margin-top: 8px; }
        .status-list { display: grid; gap: 10px; }
        .status-item {
            display: flex; justify-content: space-between; align-items: center;
            background: rgba(255,255,255,0.02); border: 1px solid var(--line); padding: 10px 12px; border-radius: 12px;
        }
        .badge {
            display: inline-block; padding: 6px 10px; border-radius: 999px; font-size: 0.78rem; font-weight: 700;
        }
        .warn { background: rgba(251,191,36,0.15); color: var(--warning); }
        .info { background: rgba(125,211,252,0.14); color: var(--info); }
        .success { background: rgba(52,211,153,0.14); color: var(--success); }
        .danger { background: rgba(248,113,113,0.14); color: var(--danger); }
        .table-wrap { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 12px 10px; border-bottom: 1px solid var(--line); text-align: right; }
        th { color: var(--muted); font-weight: 600; }
        .status-flag {
            display: inline-flex; align-items: center; justify-content: center; min-width: 110px; padding: 6px 10px;
            border-radius: 999px; font-size: 0.75rem; font-weight: 700;
        }
        .login { display: flex; gap: 10px; align-items: center; }
        .login input, .login button {
            border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px;
            background: var(--card); color: var(--text); font: inherit;
        }
        .login button { cursor: pointer; }
        .warning { background: rgba(251,191,36,0.12); color: var(--warning); }
        .info-status { background: rgba(125,211,252,0.12); color: var(--info); }
        .success-status { background: rgba(52,211,153,0.12); color: var(--success); }
        .danger-status { background: rgba(248,113,113,0.12); color: var(--danger); }
        @media (max-width: 720px) { .topbar { flex-direction: column; align-items: flex-start; } }
    </style>
</head>
<body>
    <div class="shell">
        <div class="topbar">
            <div class="brand">🛡️ لوحة التحكم الحديثة</div>
            <form class="login" id="admin-login">
                <input id="admin-token" type="password" autocomplete="current-password"
                       placeholder="رمز المشرف" aria-label="رمز المشرف" required />
                <button type="submit">عرض البيانات</button>
                <span class="meta" id="updated-at" role="status">أدخل رمز المشرف</span>
            </form>
        </div>

        <div class="grid">
            {cards}
        </div>

        <div class="section">
            <h3>ملخص سريع</h3>
            <div class="stats-row">
                <div class="pill"><span class="small">إجمالي البلاغات</span><strong id="total-reports">{total_reports}</strong></div>
                <div class="pill"><span class="small">آخر 24 ساعة</span><strong id="last24">{last24}</strong></div>
                <div class="pill"><span class="small">المستخدمين</span><strong id="total-users">{total_users}</strong></div>
                <div class="pill"><span class="small">المحجوبون</span><strong id="blocked-users">{blocked_users}</strong></div>
            </div>
        </div>

        <div class="section">
            <h3>مؤشرات سير العمل</h3>
            <div class="status-list">
                <div class="status-item"><span>بلاغات عاجلة</span><span class="badge warn" id="urgent-count">{urgent}</span></div>
                <div class="status-item"><span>بلاغات متأخرة</span><span class="badge danger" id="overdue-count">{overdue}</span></div>
                <div class="status-item"><span>بلاغات غير مسندة</span><span class="badge info" id="unassigned-count">{unassigned}</span></div>
            </div>
        </div>

        <div class="section">
            <h3>آخر البلاغات</h3>
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th>الرقم</th>
                            <th>المستخدم</th>
                            <th>الحالة</th>
                            <th>الأولوية</th>
                            <th>الوقت</th>
                        </tr>
                    </thead>
                    <tbody id="reports-body">
                        <tr><td colspan="5">سجّل الدخول لعرض البلاغات</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        let adminToken = '';
        function statusClass(label) {
            if (label === 'جديد') return 'warning';
            if (label === 'قيد المراجعة') return 'info-status';
            if (label === 'معتمد') return 'success-status';
            if (label === 'مرفوض') return 'danger-status';
            return 'info-status';
        }
        function renderRows(items) {
            const body = document.getElementById('reports-body');
            body.replaceChildren();
            if (!items || !items.length) {
                showMessage('لا توجد بيانات لعرضها');
                return;
            }
            items.forEach(function(item) {
                const row = document.createElement('tr');
                const values = [
                    item.seq || item.id || '-',
                    item.full_name || item.username || 'غير معروف',
                    item.status || '-',
                    item.priority || 'normal',
                    item.created_at ? new Date(item.created_at).toLocaleString('ar-EG') : '-'
                ];
                values.forEach(function(value, index) {
                    const cell = document.createElement('td');
                    if (index === 2) {
                        const flag = document.createElement('span');
                        flag.className = 'status-flag ' + statusClass(item.status || '');
                        flag.textContent = value;
                        cell.appendChild(flag);
                    } else {
                        cell.textContent = value;
                    }
                    row.appendChild(cell);
                });
                body.appendChild(row);
            });
        }
        function showMessage(message) {
            const body = document.getElementById('reports-body');
            body.replaceChildren();
            const row = document.createElement('tr');
            const cell = document.createElement('td');
            cell.colSpan = 5;
            cell.textContent = message;
            row.appendChild(cell);
            body.appendChild(row);
        }
        async function loadDashboard() {
            try {
                const headers = { 'x-admin-token': adminToken };
                const dashboardRes = await fetch('/api/admin/dashboard', { headers: headers });
                const dashboard = await dashboardRes.json();
                if (!dashboardRes.ok || !dashboard.ok) {
                    throw new Error(dashboard.error || 'تعذر التحقق من صلاحية الرمز');
                }
                document.getElementById('updated-at').textContent = new Date(dashboard.generated_at).toLocaleString('ar-EG');
                document.getElementById('total-reports').textContent = dashboard.summary.total_reports ?? 0;
                document.getElementById('last24').textContent = dashboard.summary.last_24_hours ?? 0;
                document.getElementById('total-users').textContent = dashboard.summary.total_users ?? 0;
                document.getElementById('blocked-users').textContent = dashboard.summary.blocked_users ?? 0;
                document.getElementById('urgent-count').textContent = dashboard.workflow.urgent ?? 0;
                document.getElementById('overdue-count').textContent = dashboard.workflow.overdue ?? 0;
                document.getElementById('unassigned-count').textContent = dashboard.workflow.unassigned ?? 0;
                dashboard.status_breakdown.forEach(function(item) {
                    const value = document.getElementById('status-' + item.tone);
                    if (value) value.textContent = item.value ?? 0;
                });

                const reportsRes = await fetch('/api/admin/reports?size=5', { headers: headers });
                const reports = await reportsRes.json();
                if (!reportsRes.ok || !reports.ok) {
                    throw new Error(reports.error || 'تعذر تحميل البلاغات');
                }
                renderRows(reports.items || []);
            } catch (err) {
                adminToken = '';
                document.getElementById('admin-token').value = '';
                document.getElementById('updated-at').textContent = 'تعذر تحميل البيانات أو أن الرمز غير صحيح';
                showMessage('تعذر تحميل البيانات أو أن الرمز غير صحيح');
                console.error('Failed to load admin dashboard:', err);
            }
        }
        document.getElementById('admin-login').addEventListener('submit', function(event) {
            event.preventDefault();
            adminToken = document.getElementById('admin-token').value;
            loadDashboard();
        });
    </script>
</body>
</html>
"""

    html = html.replace("{cards}", cards)
    html = html.replace("{total_reports}", str(stats.get("total_reports", 0)))
    html = html.replace("{last24}", str(stats.get("last_24_hours", 0)))
    html = html.replace("{total_users}", str(stats.get("total_users", 0)))
    html = html.replace("{blocked_users}", str(stats.get("blocked_users", 0)))
    html = html.replace("{urgent}", str(workflow.get("urgent", 0)))
    html = html.replace("{overdue}", str(workflow.get("overdue", 0)))
    html = html.replace("{unassigned}", str(workflow.get("unassigned", 0)))
    return html


def register_dashboard(app: Any) -> None:
    """Register modern dashboard endpoints with the same admin auth rules."""

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard_page() -> HTMLResponse:
        return HTMLResponse(render_dashboard_html({}))

    @app.get("/api/admin/dashboard")
    async def dashboard_api(request: Request) -> JSONResponse:
        refuse = admin_api._auth(request)
        if refuse:
            return refuse
        try:
            from app import main as bot_main
            db = bot_main.app.bot_data.get("db") if bot_main.app else None
            if db is None:
                return JSONResponse({"ok": False, "error": "bot_not_ready"}, status_code=503)
            return JSONResponse(await build_dashboard_payload(db))
        except Exception:
            logger.exception("Failed to build admin dashboard payload")
            return JSONResponse({"ok": False, "error": "dashboard_unavailable"}, status_code=500)
