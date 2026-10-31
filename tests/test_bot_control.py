import copy
import os
import sys
import types
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("BOT_TOKEN", "123:TEST_TOKEN")

from api.admin_api import register  # noqa: E402
from bot import control, keyboards  # noqa: E402


class FakeDB:
    def __init__(self):
        self.accounts = []
        self.bootstrap_disabled = False
        self.audit_entries = []
        self.state = {}
        self.stats = {
            "report": {"action": "report", "presses": 3, "updated_at": "2026-10-31"},
        }

    def admin_auth_snapshot(self):
        return self.accounts, self.bootstrap_disabled

    def save_admin_auth_snapshot(self, accounts, *, disable_bootstrap=False):
        self.accounts = accounts
        if disable_bootstrap:
            self.bootstrap_disabled = True

    def mutate_admin_auth_snapshot(
        self, mutation, *, disable_bootstrap=False, audit_event=None
    ):
        accounts = [dict(account) for account in self.accounts]
        result = mutation(accounts, self.bootstrap_disabled)
        if result is not False:
            if audit_event is not None:
                self.audit_entries.append(audit_event)
            self.save_admin_auth_snapshot(
                accounts, disable_bootstrap=disable_bootstrap
            )
        return result

    async def audit(self, actor_id, action, target=None, details=None):
        self.audit_entries.append((actor_id, action, target, details))

    async def get_state(self, key):
        return self.state.get(key)

    async def set_state(self, key, value):
        if value is None:
            self.state.pop(key, None)
        else:
            self.state[key] = value

    async def button_stats(self):
        return [dict(item) for item in self.stats.values()]

    async def reset_button_stats(self):
        self.stats.clear()


class ControlNormalizationTests(unittest.TestCase):
    def tearDown(self):
        control.set_reply_keyboards(None)
        control.set_commands_config(None)
        control.set_menu_button_config(None)
        control.set_settings(None)

    def test_reply_keyboards_defaults_roundtrip(self):
        defaults = control.default_reply_keyboards()
        normalized = control.normalize_reply_keyboards(defaults)
        self.assertEqual(
            set(normalized), {item["id"] for item in control.reply_keyboard_ids()}
        )
        for key, value in normalized.items():
            self.assertEqual(value["rows"], defaults[key]["rows"])
            self.assertEqual(value["placeholder"], defaults[key]["placeholder"])

    def test_reply_keyboard_rejects_unknown_type_and_long_text(self):
        layout = control.default_reply_keyboards()
        bad = copy.deepcopy(layout)
        bad["cancel"]["rows"][0][0]["type"] = "bogus"
        self.assertIsNone(control.normalize_reply_keyboards(bad))

        too_long = copy.deepcopy(layout)
        too_long["cancel"]["rows"][0][0]["text"] = "x" * 65
        self.assertIsNone(control.normalize_reply_keyboards(too_long))

    def test_renaming_reply_button_keeps_semantic_matching(self):
        layout = control.default_reply_keyboards()
        for keyboard in layout.values():
            for row in keyboard["rows"]:
                for item in row:
                    if item.get("type") == "cancel":
                        item["text"] = "تراجع عن العملية"
        control.set_reply_keyboards(layout)
        self.assertTrue(control.is_semantic_text("cancel", "تراجع عن العملية"))
        self.assertFalse(control.is_semantic_text("cancel", control.CANCEL_LABEL))
        self.assertEqual(
            control.semantic_text("cancel", "fallback"), "تراجع عن العملية"
        )

    def test_commands_reject_duplicates_and_bad_scope(self):
        cfg = control.default_commands_config()
        self.assertIsNotNone(control.normalize_commands(cfg))

        duplicated = copy.deepcopy(cfg)
        duplicated["scopes"][0]["commands"].append(
            {"command": "start", "description": "مكرر"}
        )
        self.assertIsNone(control.normalize_commands(duplicated))

        bad_scope = copy.deepcopy(cfg)
        bad_scope["scopes"][0]["scope"] = "nowhere"
        self.assertIsNone(control.normalize_commands(bad_scope))

        bad_command = copy.deepcopy(cfg)
        bad_command["scopes"][0]["commands"][0]["command"] = "Bad Command"
        self.assertIsNone(control.normalize_commands(bad_command))

    def test_removed_scopes_detects_deleted_scope(self):
        old = control.default_commands_config()
        new = copy.deepcopy(old)
        removed_item = new["scopes"].pop()
        self.assertEqual(control.removed_scopes(old, new), [removed_item])

    def test_menu_button_requires_https_for_web_app(self):
        self.assertIsNotNone(
            control.normalize_menu_button(
                {"type": "web_app", "text": "تطبيق", "url": "https://x.test"}
            )
        )
        self.assertIsNone(
            control.normalize_menu_button(
                {"type": "web_app", "text": "تطبيق", "url": "http://x.test"}
            )
        )
        self.assertIsNone(
            control.normalize_menu_button({"type": "other"})
        )

    def test_settings_validate_url_and_rights(self):
        cfg = control.default_settings()
        self.assertIsNotNone(control.normalize_settings(cfg))
        bad_url = copy.deepcopy(cfg)
        bad_url["mini_app_url"] = "http://insecure.test"
        self.assertIsNone(control.normalize_settings(bad_url))
        bad_rights = copy.deepcopy(cfg)
        bad_rights["default_admin_rights"]["can_pin_messages"] = "yes"
        self.assertIsNone(control.normalize_settings(bad_rights))

    def test_deep_links_sanitize_payload(self):
        self.assertEqual(control.sanitize_payload("a b!c_1-2"), "abc_1-2")
        links = control.deep_links("demo_bot", "promo")
        self.assertEqual(links["start"], "https://t.me/demo_bot?start=promo")
        self.assertEqual(links["startapp"], "https://t.me/demo_bot?startapp=promo")

    def test_menu_layout_legacy_buttons_still_valid(self):
        legacy = [[{"label": "بلاغ", "action": "report"}]]
        clean = keyboards.normalize_user_menu_layout(legacy)
        self.assertIsNotNone(clean)
        self.assertEqual(clean[0][0]["type"], "callback")

        bad_visibility = [
            [{"label": "بلاغ", "action": "report", "visibility": "nobody"}]
        ]
        self.assertIsNone(keyboards.normalize_user_menu_layout(bad_visibility))


class BotControlEndpointsTests(unittest.TestCase):
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
            {
                "ADMIN_API_TOKEN": "bootstrap-owner-token",
                "ADMIN_API_TOKENS_JSON": "",
            },
        )
        self.env_patch.start()
        self.client = TestClient(self.app)
        self.headers = {"Authorization": "Bearer bootstrap-owner-token"}

    def tearDown(self):
        self.client.close()
        self.env_patch.stop()
        self.modules_patch.stop()
        control.set_reply_keyboards(None)
        control.set_commands_config(None)
        control.set_menu_button_config(None)
        control.set_settings(None)

    def test_reply_keyboards_save_and_reset(self):
        loaded = self.client.get(
            "/api/admin/bot-reply-keyboards", headers=self.headers
        )
        self.assertEqual(loaded.status_code, 200)
        self.assertFalse(loaded.json()["customized"])
        self.assertIn("cancel", loaded.json()["items"])

        layout = control.default_reply_keyboards()
        layout["cancel"]["rows"][0][0]["text"] = "رجوع للخلف"
        saved = self.client.put(
            "/api/admin/bot-reply-keyboards",
            headers=self.headers,
            json={"items": layout},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json()["customized"])
        self.assertEqual(
            self.db.audit_entries[-1][1], "bot_reply_keyboards_changed"
        )
        self.assertTrue(control.is_semantic_text("cancel", "رجوع للخلف"))

        invalid = self.client.put(
            "/api/admin/bot-reply-keyboards",
            headers=self.headers,
            json={"items": {"cancel": {"rows": []}}},
        )
        self.assertEqual(invalid.status_code, 400)

        reset = self.client.delete(
            "/api/admin/bot-reply-keyboards", headers=self.headers
        )
        self.assertEqual(reset.status_code, 200)
        self.assertFalse(reset.json()["customized"])

    def test_commands_save_and_scope_delete_tracking(self):
        loaded = self.client.get("/api/admin/bot-commands", headers=self.headers)
        self.assertEqual(loaded.status_code, 200)
        cfg = loaded.json()["items"]
        self.assertTrue(cfg["scopes"])

        reduced = {"scopes": [dict(cfg["scopes"][0])]}
        saved = self.client.put(
            "/api/admin/bot-commands",
            headers=self.headers,
            json={"items": reduced},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json()["customized"])
        self.assertIn("applied", saved.json())
        self.assertEqual(self.db.audit_entries[-1][1], "bot_commands_changed")

        invalid = self.client.put(
            "/api/admin/bot-commands",
            headers=self.headers,
            json={"items": {"scopes": []}},
        )
        self.assertEqual(invalid.status_code, 400)

    def test_menu_button_save_and_reset(self):
        loaded = self.client.get(
            "/api/admin/bot-menu-button", headers=self.headers
        )
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.json()["items"]["type"], "default")

        saved = self.client.put(
            "/api/admin/bot-menu-button",
            headers=self.headers,
            json={
                "items": {
                    "type": "web_app",
                    "text": "افتح التطبيق",
                    "url": "https://example.com/app",
                }
            },
        )
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json()["customized"])

        reset = self.client.delete(
            "/api/admin/bot-menu-button", headers=self.headers
        )
        self.assertEqual(reset.status_code, 200)
        self.assertFalse(reset.json()["customized"])

    def test_settings_save_and_analytics(self):
        loaded = self.client.get("/api/admin/bot-settings", headers=self.headers)
        self.assertEqual(loaded.status_code, 200)
        self.assertIn("default_admin_rights", loaded.json()["items"])
        self.assertIn(
            "can_pin_messages", loaded.json()["items"]["default_admin_rights"]
        )

        values = loaded.json()["items"]
        values["mini_app_url"] = "https://example.com/app"
        values["default_admin_rights_customized"] = True
        saved = self.client.put(
            "/api/admin/bot-settings",
            headers=self.headers,
            json={"items": values},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json()["items"]["default_admin_rights_customized"])

        bad = dict(values)
        bad["mini_app_url"] = "http://insecure.test"
        rejected = self.client.put(
            "/api/admin/bot-settings", headers=self.headers, json={"items": bad}
        )
        self.assertEqual(rejected.status_code, 400)

        analytics = self.client.get(
            "/api/admin/bot-analytics", headers=self.headers
        )
        self.assertEqual(analytics.status_code, 200)
        self.assertEqual(analytics.json()["total"], 3)
        self.assertEqual(analytics.json()["items"][0]["action"], "report")

        cleared = self.client.delete(
            "/api/admin/bot-analytics", headers=self.headers
        )
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(cleared.json()["items"], [])

    def test_deep_links_endpoint_sanitizes_payload(self):
        response = self.client.get(
            "/api/admin/bot-links?payload=pro mo!",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["payload"], "promo")


if __name__ == "__main__":
    unittest.main()
