import io
import unittest
import zipfile

from PIL import Image

from services.chat_attachments import (
    MAX_CHAT_ATTACHMENT_BYTES,
    validate_chat_attachment,
)


class ChatAttachmentTests(unittest.TestCase):
    def _png(self):
        output = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(output, format="PNG")
        return output.getvalue()

    def _office_file(self, extension):
        required_parts = {
            ".docx": (
                "word/document.xml",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            ".xlsx": (
                "xl/workbook.xml",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            ".pptx": (
                "ppt/presentation.xml",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ),
        }
        part, content_type, _ = required_parts[extension]
        package = io.BytesIO()
        with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "[Content_Types].xml",
                (
                    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                    f'<Override PartName="/{part}" ContentType="{content_type}"/>'
                    "</Types>"
                ),
            )
            archive.writestr(part, "<document/>")
        return package.getvalue()

    def test_accepts_verified_images_and_pdf(self):
        self.assertEqual(
            validate_chat_attachment(self._png(), "photo.png"),
            ("image", "image/png"),
        )
        self.assertEqual(
            validate_chat_attachment(
                b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n",
                "report.pdf",
            ),
            ("document", "application/pdf"),
        )

    def test_accepts_only_matching_office_package_types(self):
        for extension, content_type in (
            (
                ".docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            (
                ".xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            (
                ".pptx",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ),
        ):
            with self.subTest(extension=extension):
                self.assertEqual(
                    validate_chat_attachment(
                        self._office_file(extension), f"file{extension}"
                    ),
                    ("document", content_type),
                )

    def test_rejects_spoofed_truncated_and_disallowed_files(self):
        self.assertIsNone(validate_chat_attachment(b"\x89PNG\r\n\x1a\n", "photo.png"))
        self.assertIsNone(
            validate_chat_attachment(self._png(), "photo.exe")
        )
        self.assertIsNone(
            validate_chat_attachment(b"%PDF-1.7\ntruncated", "report.pdf")
        )
        self.assertIsNone(
            validate_chat_attachment(b"PK\x03\x04", "archive.zip")
        )
        self.assertIsNone(
            validate_chat_attachment(b"%PDF-1.7\n%%EOF\n", "report.exe")
        )

    def test_rejects_files_over_the_attachment_limit(self):
        self.assertIsNone(
            validate_chat_attachment(
                b"x" * (MAX_CHAT_ATTACHMENT_BYTES + 1), "large.pdf"
            )
        )


if __name__ == "__main__":
    unittest.main()
