import tempfile
import unittest
from pathlib import Path

from database import Database


class UserChatPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(
            Path(self.temp_dir.name) / "chat.sqlite",
            backend="sqlite",
        )

    async def asyncTearDown(self):
        self.db._conn.close()
        self.temp_dir.cleanup()

    async def test_attachment_is_owned_by_its_message_user(self):
        await self.db.open_user_conversation(101)
        message_id = await self.db.add_user_message(
            101,
            "in",
            "صورة",
            media_type="image",
            filename="photo.png",
            attachment_data=b"verified-by-upload-layer",
            attachment_content_type="image/png",
        )

        self.assertEqual(
            (await self.db.get_user_message_attachment(101, message_id))["data"],
            b"verified-by-upload-layer",
        )
        self.assertIsNone(await self.db.get_user_message_attachment(202, message_id))
        self.assertTrue((await self.db.list_user_messages(101))[0]["has_attachment"])

    async def test_delete_conversation_removes_messages_and_records_reason(self):
        await self.db.open_user_conversation(101)
        await self.db.add_user_message(
            101,
            "in",
            "مرفق",
            media_type="document",
            filename="report.pdf",
            attachment_data=b"pdf-content",
            attachment_content_type="application/pdf",
        )

        self.assertTrue(await self.db.delete_user_conversation(101, 9002, "طلب المستخدم"))
        self.assertFalse(await self.db.list_user_messages(101))
        self.assertIsNone(await self.db.get_user_message_attachment(101, 1))
        row = self.db._conn.execute(
            "SELECT action, details FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(row["action"], "user_conversation_deleted")
        self.assertIn("طلب المستخدم", row["details"])

    async def test_contact_preferences_default_on_and_can_be_disabled(self):
        defaults = await self.db.user_contact_preferences(101)
        self.assertEqual(
            defaults,
            {"telegram_reply_alerts": True, "publication_alerts": True},
        )

        await self.db.set_user_contact_preferences(101, False, False)
        self.assertEqual(
            await self.db.user_contact_preferences(101),
            {"telegram_reply_alerts": False, "publication_alerts": False},
        )


if __name__ == "__main__":
    unittest.main()
