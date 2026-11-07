import json
import os
import sys
import types
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("BOT_TOKEN", "123:TEST_TOKEN")

from api.admin_api import register  # noqa: E402


class FakeDB:
    def __init__(self):
        self.state = {}
        self.audit_entries = []

    def admin_auth_snapshot(self):
        return [], False

    async def get_state(self, key):
        return self.state.get(key)

    async def set_state(self, key, value):
        if value is None:
            self.state.pop(key, None)
        else:
            self.state[key] = value

    async def audit(self, actor_id, action, target=None, details=None):
        self.audit_entries.append((actor_id, action, target, details))


class MobileAppPolicyTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.app = FastAPI()
        register(self.app)
        bot_main = types.ModuleType("app.main")
        bot_main.app = types.SimpleNamespace(bot_data={"db": self.db})
        self.modules_patch = patch.dict(sys.modules, {"app.main": bot_main})
        self.modules_patch.start()
        self.env_patch = patch.dict(
            os.environ,
            {"ADMIN_API_TOKEN": "bootstrap-owner-token", "ADMIN_API_TOKENS_JSON": ""},
        )
        self.env_patch.start()
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.env_patch.stop()
        self.modules_patch.stop()

    def test_mobile_policy_is_public_and_defaults_to_active(self):
        response = self.client.get("/api/mobile/policy")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "active")
        self.assertEqual(response.json()["latest_version"], "")
        self.assertEqual(response.json()["apk_url"], "")

    def test_owner_can_save_https_release_and_it_is_audited(self):
        policy = {
            "status": "active",
            "status_message": "",
            "latest_version": "2.7.20261107",
            "update_mode": "required",
            "apk_url": "https://downloads.example.com/admin.apk",
            "sha256": "a" * 64,
            "release_notes": "تحسينات وإصلاحات.",
        }

        response = self.client.put(
            "/api/admin/mobile-app",
            headers={"Authorization": "Bearer bootstrap-owner-token"},
            json=policy,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["policy"], policy)
        self.assertEqual(self.db.audit_entries[0][1], "mobile_app_policy_changed")
        self.assertEqual(self.client.get("/api/mobile/policy").json()["sha256"], "a" * 64)

    def test_rejects_non_https_or_incomplete_release(self):
        policy = {
            "status": "active",
            "status_message": "",
            "latest_version": "2.7.20261107",
            "update_mode": "optional",
            "apk_url": "http://downloads.example.com/admin.apk",
            "sha256": "a" * 64,
            "release_notes": "",
        }

        response = self.client.put(
            "/api/admin/mobile-app",
            headers={"Authorization": "Bearer bootstrap-owner-token"},
            json=policy,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_mobile_app_policy")

    def test_mobile_app_settings_are_owner_only(self):
        os.environ["ADMIN_API_TOKENS_JSON"] = json.dumps([
            {"token": "viewer-token", "role": "viewer"},
        ])

        response = self.client.get(
            "/api/admin/mobile-app",
            headers={"Authorization": "Bearer viewer-token"},
        )

        self.assertEqual(response.status_code, 403)

    def test_mobile_admin_requests_are_blocked_when_paused(self):
        self.db.state["mobile_app_policy"] = json.dumps({
            "status": "paused",
            "status_message": "تحديث مؤقت",
        })

        blocked = self.client.get(
            "/api/admin/session",
            headers={
                "Authorization": "Bearer bootstrap-owner-token",
                "X-Admin-Client": "android",
            },
        )
        desktop = self.client.get(
            "/api/admin/session",
            headers={"Authorization": "Bearer bootstrap-owner-token"},
        )

        self.assertEqual(blocked.status_code, 503)
        self.assertEqual(blocked.json()["error"], "mobile_app_paused")
        self.assertEqual(desktop.status_code, 200)
