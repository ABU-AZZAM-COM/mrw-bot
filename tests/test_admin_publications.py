import os
import unittest

os.environ.setdefault("BOT_TOKEN", "123:TEST_TOKEN")

from api.admin_api import _publication_delivery_text
from bot import texts
from services import utils


class PublicationDeliveryTextTests(unittest.TestCase):
    def test_text_publication_matches_broadcast_template_and_escapes_html(self):
        caption = "تحديث <مهم> & جديد"

        result = _publication_delivery_text(caption, "")

        self.assertEqual(
            result,
            texts.BROADCAST_NOTIF.format(text=utils.esc(caption)),
        )
        self.assertIn("&lt;مهم&gt;", result)

    def test_media_publication_caption_matches_recipient_message(self):
        self.assertEqual(
            _publication_delivery_text("تحديث جديد", "photo"),
            "📨 منشور من الجهة المختصة\n\nتحديث جديد",
        )

    def test_media_publication_without_caption_keeps_sender_heading(self):
        self.assertEqual(
            _publication_delivery_text("", "document"),
            "📨 منشور من الجهة المختصة",
        )


if __name__ == "__main__":
    unittest.main()
