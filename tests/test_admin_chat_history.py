import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("BOT_TOKEN", "123:TEST_TOKEN")

from api.admin_api import register
from database import Database


class AdminChatHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(
            Path(self.temp_dir.name) / "chat.sqlite",
            backend="sqlite",
        )
        self.bot_main = types.ModuleType("app.main")
        self.bot_main.app = types.SimpleNamespace(bot_data={"db": self.db})
        self.modules_patch = patch.dict(sys.modules, {"app.main": self.bot_main})
        self.modules_patch.start()
        self.env_patch = patch.dict(
            os.environ,
            {
                "ADMIN_API_TOKEN": "chat-history-test-token",
                "ADMIN_API_TOKENS_JSON": "",
            },
        )
        self.env_patch.start()
        self.app = FastAPI()
        register(self.app)
        self.client = TestClient(self.app)
        self.headers = {"X-Admin-Token": "chat-history-test-token"}

    def tearDown(self):
        self.client.close()
        self.env_patch.stop()
        self.modules_patch.stop()
        self.db._conn.close()
        self.temp_dir.cleanup()

    def _add_user(self, user_id):
        self.db._conn.execute(
            """
            INSERT INTO bot_users
                (user_id, username, full_name, first_seen_at, last_seen_at)
            VALUES (?, NULL, ?, '2026-01-01T00:00:00+00:00',
                    '2026-01-01T00:00:00+00:00')
            """,
            (user_id, f"User {user_id}"),
        )
        self.db._conn.commit()

    def test_admin_history_and_conversation_list_hide_bot_generated_text(self):
        import asyncio

        self._add_user(101)
        asyncio.run(self.db.open_user_conversation(101))
        asyncio.run(self.db.add_user_message(101, "in", "رسالة المستخدم"))
        asyncio.run(
            self.db.add_user_message(
                101, "out", "تنبيه آلي", telegram_message_id=31
            )
        )
        asyncio.run(
            self.db.add_user_message(
                101,
                "out",
                "رد الإدارة",
                admin_id=9002,
                telegram_message_id=32,
            )
        )
        self._add_user(202)
        asyncio.run(self.db.open_user_conversation(202))
        asyncio.run(
            self.db.add_user_message(
                202, "out", "رسالة آلية بلا محادثة", telegram_message_id=41
            )
        )

        history = self.client.get(
            "/api/admin/users/101/messages",
            headers=self.headers,
        )
        conversations = self.client.get(
            "/api/admin/conversations",
            headers=self.headers,
        )

        self.assertEqual(history.status_code, 200)
        self.assertEqual(
            [item["text"] for item in history.json()["items"]],
            ["رسالة المستخدم", "رد الإدارة"],
        )
        self.assertEqual(conversations.status_code, 200)
        self.assertEqual(
            [item["user_id"] for item in conversations.json()["items"]],
            [101],
        )
        self.assertEqual(
            conversations.json()["items"][0]["last_message"],
            "رد الإدارة",
        )

    def test_admin_history_without_cursor_starts_with_latest_messages(self):
        import asyncio

        self._add_user(101)
        asyncio.run(self.db.open_user_conversation(101))
        ids = [
            asyncio.run(
                self.db.add_user_message(101, "in", f"رسالة {index}")
            )
            for index in range(105)
        ]

        latest = self.client.get(
            "/api/admin/users/101/messages",
            headers=self.headers,
        )
        from_start = self.client.get(
            "/api/admin/users/101/messages?after_id=0",
            headers=self.headers,
        )

        self.assertEqual(latest.status_code, 200)
        self.assertEqual(
            [item["id"] for item in latest.json()["items"]],
            ids[-100:],
        )
        self.assertEqual(
            [item["id"] for item in from_start.json()["items"]],
            ids[:100],
        )


if __name__ == "__main__":
    unittest.main()
