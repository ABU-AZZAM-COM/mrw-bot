import asyncio
import hashlib
import hmac
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from database import Database
from app import space_app


class UserChatApiTests(unittest.TestCase):
    BOT_TOKEN = "123:TEST_TOKEN"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(
            Path(self.temp_dir.name) / "chat.sqlite",
            backend="sqlite",
        )
        self.application = SimpleNamespace(bot_data={"db": self.db}, bot=None)
        self.previous_admin_web_enabled = os.environ.get("ADMIN_WEB_ENABLED")
        os.environ["ADMIN_WEB_ENABLED"] = "0"
        self.token_patch = patch.object(space_app, "BOT_TOKEN", self.BOT_TOKEN)
        self.bot_patch = patch.object(space_app.bot_main, "app", self.application)
        self.token_patch.start()
        self.bot_patch.start()
        self.client = TestClient(space_app.build_app())

    def tearDown(self):
        self.client.close()
        self.bot_patch.stop()
        self.token_patch.stop()
        if self.previous_admin_web_enabled is None:
            os.environ.pop("ADMIN_WEB_ENABLED", None)
        else:
            os.environ["ADMIN_WEB_ENABLED"] = self.previous_admin_web_enabled
        self.db._conn.close()
        self.temp_dir.cleanup()

    def _init_data(self, user_id):
        fields = {
            "auth_date": str(int(time.time())),
            "query_id": f"query-{user_id}",
            "user": json.dumps({"id": user_id, "first_name": "Test"}),
        }
        secret = hmac.new(
            b"WebAppData", self.BOT_TOKEN.encode(), hashlib.sha256
        ).digest()
        check_string = "\n".join(
            f"{key}={value}" for key, value in sorted(fields.items())
        )
        fields["hash"] = hmac.new(
            secret, check_string.encode(), hashlib.sha256
        ).hexdigest()
        return urlencode(fields)

    def _headers(self, user_id):
        return {"X-Telegram-Init-Data": self._init_data(user_id)}

    def _png(self):
        output = io.BytesIO()
        Image.new("RGB", (2, 2), "blue").save(output, format="PNG")
        return output.getvalue()

    def test_preferences_are_signed_per_user_and_saved(self):
        headers = self._headers(101)
        response = self.client.get("/api/user/settings", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["preferences"]["telegram_reply_alerts"])
        self.assertTrue(response.json()["preferences"]["publication_alerts"])

        updated = self.client.put(
            "/api/user/settings",
            headers=headers,
            json={
                "telegram_reply_alerts": False,
                "publication_alerts": True,
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(
            asyncio.run(self.db.user_contact_preferences(101)),
            {
                "telegram_reply_alerts": False,
                "publication_alerts": True,
            },
        )

    def test_attachment_is_owned_by_signed_telegram_user(self):
        response = self.client.post(
            "/api/user/messages/attachment",
            headers=self._headers(101),
            files={"file": ("proof.png", self._png(), "image/png")},
            data={"text": "دليل"},
        )
        self.assertEqual(response.status_code, 200)
        message_id = response.json()["message_id"]

        own_download = self.client.get(
            f"/api/user/messages/{message_id}/attachment",
            headers=self._headers(101),
        )
        other_download = self.client.get(
            f"/api/user/messages/{message_id}/attachment",
            headers=self._headers(202),
        )
        self.assertEqual(own_download.status_code, 200)
        self.assertEqual(own_download.content, self._png())
        self.assertEqual(other_download.status_code, 404)

    def test_global_contact_off_blocks_sending_but_not_history(self):
        asyncio.run(self.db.open_user_conversation(101))
        asyncio.run(self.db.add_user_message(101, "in", "رسالة محفوظة"))
        asyncio.run(self.db.set_state("contact_global_enabled", "0"))
        headers = self._headers(101)

        history = self.client.get("/api/user/messages", headers=headers)
        blocked_send = self.client.post(
            "/api/user/messages",
            headers=headers,
            json={"text": "رسالة جديدة"},
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.json()["items"]), 1)
        self.assertEqual(blocked_send.status_code, 403)


if __name__ == "__main__":
    unittest.main()
