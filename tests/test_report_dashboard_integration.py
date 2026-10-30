import asyncio
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("BOT_TOKEN", "123:TEST_TOKEN")

from api.admin_api import register
from bot import handlers
from database import Database


class ReportDashboardIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "reports.sqlite", backend="sqlite")
        self.app = FastAPI()
        register(self.app)
        bot_main = types.ModuleType("app.main")
        bot_main.app = SimpleNamespace(bot_data={"db": self.db})
        self.modules_patch = patch.dict(sys.modules, {"app.main": bot_main})
        self.modules_patch.start()
        self.env_patch = patch.dict(os.environ, {
            "ADMIN_API_TOKEN": "local-integration-test-token",
            "ADMIN_API_TOKENS_JSON": "",
        })
        self.env_patch.start()
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.env_patch.stop()
        self.modules_patch.stop()
        self.db.close()
        self.temp_dir.cleanup()

    def test_bot_report_appears_in_admin_reports_and_notifications(self):
        reporter = SimpleNamespace(
            id=987654321,
            username="synthetic_reporter",
            full_name="Synthetic Test Reporter",
        )
        message = SimpleNamespace(reply_text=AsyncMock())
        bot = SimpleNamespace(
            send_message=AsyncMock(),
            send_photo=AsyncMock(),
        )
        update = SimpleNamespace(
            effective_user=reporter,
            effective_message=message,
            callback_query=None,
        )
        context = SimpleNamespace(
            bot=bot,
            bot_data={"db": self.db},
            user_data={
                "description": "LOCAL-INTEGRATION-TEST-9E2: synthetic report; no action required",
                "category": "other",
                "violation_type": "local integration test",
            },
        )

        with (
            patch.object(handlers.config, "ADMIN_IDS", [1357911]),
            patch.object(handlers.config, "AUTO_PUBLISH_TO_ARCHIVE", False),
            patch.object(handlers.config, "DB_BACKEND", "test"),
            patch.object(handlers.hub_backup, "is_configured", return_value=False),
        ):
            asyncio.run(handlers.confirm_save(update, context))

        message.reply_text.assert_awaited_once()
        bot.send_message.assert_awaited_once()
        self.assertEqual(bot.send_message.await_args.kwargs["chat_id"], 1357911)
        self.assertIn(
            "LOCAL-INTEGRATION-TEST-9E2",
            bot.send_message.await_args.kwargs["text"],
        )
        self.assertIn("last_saved", context.user_data)

        headers = {"X-Admin-Token": "local-integration-test-token"}
        reports = self.client.get(
            "/api/admin/reports",
            params={"q": "LOCAL-INTEGRATION-TEST-9E2"},
            headers=headers,
        )
        self.assertEqual(reports.status_code, 200, reports.text)
        report_payload = reports.json()
        self.assertTrue(report_payload["ok"])
        self.assertEqual(report_payload["total"], 1)
        report = report_payload["items"][0]
        self.assertIn("LOCAL-INTEGRATION-TEST-9E2", report["description"])
        self.assertEqual(report["user_id"], reporter.id)

        detail = self.client.get(
            f"/api/admin/reports/{report['id']}",
            headers=headers,
        )
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["item"]["seq"], report["seq"])

        notifications = self.client.get(
            "/api/admin/notifications",
            headers=headers,
        )
        self.assertEqual(notifications.status_code, 200, notifications.text)
        self.assertTrue(
            any(
                item["type"] == "report_new"
                and item["payload"].get("report_id") == report["id"]
                for item in notifications.json()["items"]
            )
        )


if __name__ == "__main__":
    unittest.main()
