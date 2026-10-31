import asyncio
import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import Database
from bot import control, keyboards, texts
from services import guards


class BotTextSeedTests(unittest.TestCase):
    def test_full_catalog_is_seeded_and_database_edits_are_applied(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "catalog.sqlite", backend="sqlite")
            original_followup = texts.INLINE_MESSAGES["followup_length"]
            original_category = keyboards.REPORT_CATEGORIES["conduct"]
            original_text_globals = {
                name: copy.deepcopy(value)
                for name, value in vars(texts).items()
                if name.isupper() and isinstance(value, (str, dict))
            }
            original_runtime_texts = dict(texts._TEXT_RUNTIME)
            original_fallback_texts = dict(texts._TEXT_FALLBACKS)
            original_control_labels = {
                key.removeprefix("bot.control."): getattr(
                    control, key.removeprefix("bot.control.")
                )
                for key in texts._catalog_defaults()
                if key.startswith("bot.control.")
            }
            try:
                loaded = asyncio.run(texts.load_database_texts(db))
                rows = db.get_all_bot_texts_sync()
                keys = {item["key"] for item in rows}

                self.assertGreater(loaded, 100)
                self.assertIn("bot.ASK_DESC", keys)
                self.assertIn("bot.CATEGORY_INTROS.field", keys)
                self.assertIn("bot.BLOCK_MESSAGES.frozen", keys)
                self.assertIn("bot.INLINE_MESSAGES.followup_length", keys)
                self.assertIn("bot.INLINE_MESSAGES.link_invalid_http", keys)
                self.assertIn("bot.INLINE_MESSAGES.verification_supplement_notification", keys)
                self.assertIn("bot.VERIFICATION_DOCUMENT_LABELS.id_front", keys)
                self.assertIn("welcome_message", keys)
                self.assertNotIn("bot.WELCOME", keys)
                self.assertIn("bot.control.CANCEL_LABEL", keys)
                self.assertIn("bot.keyboards.USER_MENU_ACTIONS.report", keys)
                self.assertIn("bot.keyboards.SOCIAL_PLATFORM_LABELS.telegram", keys)
                consent = next(
                    item["content"]
                    for item in rows
                    if item["key"] == "verify_consent"
                )
                status_badge_key = next(
                    key for key in keys if key.startswith("bot.STATUS_BADGE.")
                )
                status_name = status_badge_key.rsplit(".", 1)[-1]

                db.update_bot_text_sync(
                    "bot.ASK_DESC", "وصف مخصص بحد {maxlen}", "user_facing"
                )
                db.update_bot_text_sync(
                    "welcome_message", "ترحيب مخصص", "user_facing"
                )
                db.update_bot_text_sync(
                    "bot.INLINE_MESSAGES.followup_length",
                    "أدخل معلومات صحيحة",
                    "user_facing",
                )
                db.update_bot_text_sync(
                    "bot.INLINE_MESSAGES.link_invalid_http",
                    "رابط غير مقبول",
                    "user_facing",
                )
                db.update_bot_text_sync(
                    "bot.INLINE_MESSAGES.description_too_short",
                    "وصف قصير {unknown}",
                    "user_facing",
                )
                db.update_bot_text_sync(
                    status_badge_key,
                    "شارة مخصصة",
                    "user_facing",
                )
                db.update_bot_text_sync(
                    "bot.keyboards.REPORT_CATEGORIES.conduct",
                    "🚨 تصنيف مخصص",
                    "user_facing",
                )
                asyncio.run(
                    texts.load_database_texts(db, seed_defaults=False)
                )

                self.assertEqual(texts.ASK_DESC, "وصف مخصص بحد {maxlen}")
                self.assertEqual(texts.WELCOME, "ترحيب مخصص")
                self.assertEqual(
                    texts.INLINE_MESSAGES["followup_length"], "أدخل معلومات صحيحة"
                )
                self.assertEqual(
                    guards.check_link("ftp://example.com").message,
                    "رابط غير مقبول",
                )
                self.assertEqual(
                    texts.format_inline_message(
                        "description_too_short", min_length=20
                    ),
                    "وصف قصير {unknown}",
                )
                self.assertEqual(texts.badge(status_name), "شارة مخصصة")
                self.assertFalse(texts.is_catalog_key("bot.NOT_A_TEXT"))
                self.assertEqual(
                    keyboards.REPORT_CATEGORIES["conduct"], "🚨 تصنيف مخصص"
                )
                db.update_bot_text_sync(
                    "bot.INLINE_MESSAGES.followup_length", "", "user_facing"
                )
                asyncio.run(
                    texts.load_database_texts(db, seed_defaults=False)
                )
                self.assertEqual(
                    texts.INLINE_MESSAGES["followup_length"],
                    original_followup,
                )
                self.assertTrue(texts.apply_database_text("bot.ASK_DESC", ""))
                self.assertEqual(
                    texts.ASK_DESC, original_text_globals["ASK_DESC"]
                )
                self.assertEqual(
                    asyncio.run(
                        texts.get_text(
                            "verify_consent", texts.VERIFICATION_CONSENT_DEFAULT
                        )
                    ),
                    consent,
                )
            finally:
                for name, value in original_text_globals.items():
                    setattr(texts, name, value)
                texts._TEXT_RUNTIME.clear()
                texts._TEXT_RUNTIME.update(original_runtime_texts)
                texts._TEXT_FALLBACKS.clear()
                texts._TEXT_FALLBACKS.update(original_fallback_texts)
                keyboards.REPORT_CATEGORIES["conduct"] = original_category
                for name, value in original_control_labels.items():
                    setattr(control, name, value)
                db.close()

    def test_seed_repairs_defaults_without_overwriting_custom_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bot-texts.sqlite"
            db = Database(path, backend="sqlite")
            db.close()

            legacy_welcome = (
                "🛡️ منظومة البلاغات والشكاوى\nإدارة الشرطة العسكرية\n"
                "────────────────────\nأهلًا بك. يمكنك إرسال بلاغ ومتابعة حالته "
                "من هذه المحادثة.\nستراجع بياناتك قبل الإرسال، ثم يصلك رقم تتبع "
                "للمتابعة.\n\nمشاركة رقم الهاتف والموقع اختيارية. أرسل فقط "
                "المعلومات والأدلة ذات الصلة.\n\nاختر الخدمة التي تحتاجها:"
            )
            connection = sqlite3.connect(path)
            try:
                with connection:
                    connection.execute(
                        "UPDATE bot_texts SET content = ? WHERE key = ?",
                        (legacy_welcome, "welcome_message"),
                    )
                    connection.execute(
                        "UPDATE bot_texts SET content = '' WHERE key = ?",
                        ("help_text",),
                    )
                    connection.execute(
                        "UPDATE bot_texts SET content = ? WHERE key = ?",
                        ("نص خصصه المشرف", "privacy_policy"),
                    )
                    connection.execute(
                        "DELETE FROM bot_texts WHERE key = ?", ("status_update",)
                    )
            finally:
                connection.close()

            reopened = Database(path, backend="sqlite")
            values = {
                item["key"]: item["content"]
                for item in reopened.get_all_bot_texts_sync()
            }
            reopened.close()

            self.assertIn("<b>منظومة البلاغات والشكاوى</b>", values["welcome_message"])
            self.assertIn("<b>الدليل السريع</b>", values["help_text"])
            self.assertEqual(values["privacy_policy"], "نص خصصه المشرف")
            self.assertIn("{seq}", values["status_update"])

    def test_seed_leaves_existing_custom_defaults_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bot-texts.sqlite"
            db = Database(path, backend="sqlite")
            db.update_bot_text_sync(
                "welcome_message", "رسالة مخصصة من المشرف", "user_facing"
            )
            db.close()

            reopened = Database(path, backend="sqlite")
            self.assertEqual(
                reopened.get_bot_text_sync("welcome_message"),
                "رسالة مخصصة من المشرف",
            )
            reopened.close()


if __name__ == "__main__":
    unittest.main()
