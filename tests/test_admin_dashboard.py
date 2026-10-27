import asyncio
import os
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin_dashboard import (
    build_dashboard_payload,
    register_dashboard,
    render_dashboard_html,
)


class FakeDB:
    async def count_by_status(self):
        return {"جديد": 3, "قيد المراجعة": 4, "معتمد": 2, "مرفوض": 1}

    async def count_since(self, iso):
        return 9

    async def workflow_counts(self):
        return {"urgent": 1, "overdue": 2, "unassigned": 3}

    async def total_users(self):
        return 42

    async def total(self):
        return 10

    async def blocked_users_count(self):
        return 5


class AdminDashboardTests(unittest.TestCase):
    def test_build_dashboard_payload(self):
        result = asyncio.run(build_dashboard_payload(FakeDB()))
        self.assertTrue(result["ok"])
        self.assertEqual(result["summary"]["total_reports"], 10)
        self.assertEqual(result["summary"]["new_reports"], 3)
        self.assertEqual(result["workflow"]["unassigned"], 3)

    def test_render_dashboard_html_contains_core_sections(self):
        html = render_dashboard_html({
            "generated_at": "2026-01-01T00:00:00+00:00",
            "summary": {"total_reports": 12, "last_24_hours": 7, "total_users": 21, "blocked_users": 3},
            "workflow": {"urgent": 1, "overdue": 2, "unassigned": 4},
            "status_breakdown": [
                {"label": "جديد", "value": 5, "tone": "warning"},
                {"label": "معتمد", "value": 7, "tone": "success"},
            ],
        })
        self.assertIn("لوحة التحكم الحديثة", html)
        self.assertIn("إجمالي البلاغات", html)
        self.assertIn("مؤشرات سير العمل", html)

    def test_dashboard_page_does_not_embed_report_statistics(self):
        app = FastAPI()
        register_dashboard(app)

        bot_main = types.ModuleType("main")
        bot_main.app = types.SimpleNamespace(bot_data={"db": FakeDB()})
        with patch.dict(sys.modules, {"main": bot_main}), patch(
            "admin_dashboard.build_dashboard_payload",
            new_callable=AsyncMock,
        ) as build_payload:
            response = TestClient(app).get("/dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertIn("رمز المشرف", response.text)
        build_payload.assert_not_awaited()

    def test_dashboard_api_requires_admin_token(self):
        app = FastAPI()
        register_dashboard(app)

        with patch.dict(os.environ, {
            "ADMIN_API_TOKEN": "",
            "ADMIN_API_TOKENS_JSON": "",
        }):
            response = TestClient(app).get("/api/admin/dashboard")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], "api_token_not_configured")

    def test_dashboard_api_does_not_accept_token_from_query_string(self):
        app = FastAPI()
        register_dashboard(app)

        with patch.dict(os.environ, {
            "ADMIN_API_TOKEN": "test-token",
            "ADMIN_API_TOKENS_JSON": "",
        }):
            response = TestClient(app).get(
                "/api/admin/dashboard?token=test-token"
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "unauthorized")

    def test_dashboard_api_returns_data_for_valid_admin_token(self):
        app = FastAPI()
        register_dashboard(app)
        bot_main = types.ModuleType("main")
        bot_main.app = types.SimpleNamespace(bot_data={"db": FakeDB()})

        with patch.dict(
            sys.modules,
            {"main": bot_main},
        ), patch.dict(os.environ, {
            "ADMIN_API_TOKEN": "test-token",
            "ADMIN_API_TOKENS_JSON": "",
        }):
            response = TestClient(app).get(
                "/api/admin/dashboard",
                headers={"x-admin-token": "test-token"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"]["total_reports"], 10)


if __name__ == "__main__":
    unittest.main()
