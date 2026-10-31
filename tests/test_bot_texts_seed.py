import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import Database


class BotTextSeedTests(unittest.TestCase):
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
