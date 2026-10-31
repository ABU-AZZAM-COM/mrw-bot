import asyncio
import copy
import json
import os
import sys
import threading
import types
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("BOT_TOKEN", "123:TEST_TOKEN")

from api.admin_api import register  # noqa: E402
from bot import control, keyboards, texts  # noqa: E402


class FakeDB:
    def __init__(self):
        self.accounts = []
        self.bootstrap_disabled = False
        self.audit_entries = []
        self.state = {}
        self.stats = {
            "report": {"action": "report", "presses": 3, "updated_at": "2026-10-31"},
        }
        self.bot_texts = {}

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

    async def update_bot_text(self, key, content, category="user_facing"):
        self.bot_texts[key] = {
            "key": key,
            "content": content,
            "category": category,
        }
        return True

    async def get_bot_text(self, key):
        item = self.bot_texts.get(key)
        return item.get("content") if item else None

    async def get_all_bot_texts(self):
        return list(self.bot_texts.values())


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
        self.bot_app = bot_main.app
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
        keyboards.set_user_menu_layout(None)

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

    def test_text_update_applies_to_live_catalog_and_rejects_unknown_key(self):
        original = texts.ASK_DESC
        loop = asyncio.new_event_loop()
        ready = threading.Event()
        loop.call_soon(ready.set)
        bot_thread = threading.Thread(
            target=lambda: (asyncio.set_event_loop(loop), loop.run_forever()),
            daemon=True,
        )
        bot_thread.start()
        self.assertTrue(ready.wait(timeout=2))
        self.bot_app.bot = object()
        self.bot_app.bot_data["loop"] = loop
        apply_threads = []
        original_apply = texts.apply_database_text

        def capture_apply_thread(key, content):
            apply_threads.append(threading.get_ident())
            return original_apply(key, content)

        try:
            with patch.object(texts, "apply_database_text", capture_apply_thread):
                response = self.client.post(
                    "/admin_api/texts/update",
                    headers=self.headers,
                    json={
                        "key": "bot.ASK_DESC",
                        "content": "نص تحديث مباشر {maxlen}",
                        "label": "وصف البلاغ",
                        "category": "user_facing",
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["applied"])
                self.assertEqual(texts.ASK_DESC, "نص تحديث مباشر {maxlen}")

                reset = self.client.post(
                    "/admin_api/texts/update",
                    headers=self.headers,
                    json={
                        "key": "bot.ASK_DESC",
                        "content": "",
                        "label": "وصف البلاغ",
                    },
                )
                self.assertEqual(reset.status_code, 200)
                self.assertTrue(reset.json()["reset_to_default"])
                self.assertEqual(texts.ASK_DESC, original)

            rejected = self.client.post(
                "/admin_api/texts/update",
                headers=self.headers,
                json={
                    "key": "bot.NOT_A_TEXT",
                    "content": "يجب ألا يُحفظ",
                },
            )
            self.assertEqual(rejected.status_code, 404)
            self.assertNotIn("bot.NOT_A_TEXT", self.db.bot_texts)
            self.assertEqual(apply_threads, [bot_thread.ident, bot_thread.ident])
        finally:
            loop.call_soon_threadsafe(loop.stop)
            bot_thread.join(timeout=2)
            loop.close()
            self.bot_app.bot_data.pop("loop", None)
            del self.bot_app.bot
            texts.ASK_DESC = original
            texts._TEXT_RUNTIME.pop("bot.ASK_DESC", None)

    def test_text_catalog_returns_editable_defaults_when_database_is_empty(self):
        response = self.client.get("/admin_api/texts", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        catalog = response.json()["texts"]
        keys = [item["key"] for item in catalog]
        self.assertGreater(len(keys), 100)
        self.assertEqual(keys, sorted(keys))
        self.assertIn("bot.INLINE_MESSAGES.followup_length", keys)
        self.assertFalse(any(key.startswith("bot.STATUS_EMOJI.") for key in keys))

    def test_text_catalog_uses_runtime_fallbacks_and_omits_obsolete_keys(self):
        self.db.bot_texts["bot.ASK_DESC"] = {
            "key": "bot.ASK_DESC",
            "label": "وصف البلاغ",
            "content": "",
            "category": "user_facing",
        }
        self.db.bot_texts["bot.STATUS_EMOJI.legacy"] = {
            "key": "bot.STATUS_EMOJI.legacy",
            "label": "رمز قديم",
            "content": "غير مستخدم",
            "category": "user_facing",
        }
        response = self.client.get("/admin_api/texts", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        catalog = response.json()["texts"]
        self.assertGreater(len(catalog), 100)
        ask_desc = next(item for item in catalog if item["key"] == "bot.ASK_DESC")
        self.assertTrue(ask_desc["content"])
        self.assertFalse(any(item["key"] == "bot.STATUS_EMOJI.legacy" for item in catalog))

    def test_text_catalog_reads_the_effective_welcome_and_menu_button(self):
        layout = [[{"label": "زر القائمة الفعلي", "action": "report"}]]
        self.db.state[keyboards.USER_MENU_STATE_KEY] = json.dumps(
            layout, ensure_ascii=False
        )
        self.db.state["bot_profile"] = json.dumps(
            {"welcome_text": "رسالة الترحيب الفعلية"}, ensure_ascii=False
        )
        response = self.client.get("/admin_api/texts", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        catalog = {item["key"]: item for item in response.json()["texts"]}
        self.assertEqual(
            catalog["bot.keyboards.USER_MENU_ACTIONS.report"]["content"],
            "زر القائمة الفعلي",
        )
        self.assertEqual(
            catalog["welcome_message"]["content"], "رسالة الترحيب الفعلية"
        )
        self.assertNotIn("bot.BOT_PROFILE_DEFAULT_WELCOME", catalog)

    def test_editing_menu_button_updates_the_persisted_live_layout(self):
        self.db.state[keyboards.USER_MENU_STATE_KEY] = json.dumps(
            [[{"label": "قديم", "action": "report"}]], ensure_ascii=False
        )
        response = self.client.post(
            "/admin_api/texts/update",
            headers=self.headers,
            json={
                "key": "bot.keyboards.USER_MENU_ACTIONS.report",
                "content": "تقديم بلاغ",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["applied"])
        layout = keyboards.normalize_user_menu_layout(
            self.db.state[keyboards.USER_MENU_STATE_KEY]
        )
        self.assertEqual(layout[0][0]["label"], "تقديم بلاغ")
        self.assertEqual(keyboards.current_user_menu_layout()[0][0]["label"], "تقديم بلاغ")

    def test_editing_reply_button_updates_saved_keyboard_not_only_default(self):
        layout = control.default_reply_keyboards()
        layout["cancel"]["rows"][0][0]["text"] = "زر الإلغاء المخصص"
        self.db.state[control.REPLY_STATE_KEY] = json.dumps(
            layout, ensure_ascii=False
        )
        response = self.client.post(
            "/admin_api/texts/update",
            headers=self.headers,
            json={
                "key": "bot.control.CANCEL_LABEL",
                "content": "إلغاء العملية",
            },
        )
        self.assertEqual(response.status_code, 200)
        saved = control.normalize_reply_keyboards(
            self.db.state[control.REPLY_STATE_KEY]
        )
        self.assertEqual(saved["cancel"]["rows"][0][0]["text"], "إلغاء العملية")
        self.assertEqual(
            control.current_reply_keyboards()["cancel"]["rows"][0][0]["text"],
            "إلغاء العملية",
        )

    def test_editing_welcome_clears_the_shadowing_profile_override(self):
        original = texts.WELCOME
        self.db.state["bot_profile"] = json.dumps(
            {
                "name": "Bot",
                "welcome_text": "نص ملف البوت القديم",
                "privacy_policy_url": "",
            },
            ensure_ascii=False,
        )
        response = self.client.post(
            "/admin_api/texts/update",
            headers=self.headers,
            json={"key": "welcome_message", "content": "ترحيب جديد"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(texts.WELCOME, "ترحيب جديد")
        profile = json.loads(self.db.state["bot_profile"])
        self.assertNotIn("welcome_text", profile)
        texts.WELCOME = original

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
