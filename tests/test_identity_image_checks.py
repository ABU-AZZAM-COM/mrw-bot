import os
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from cryptography.fernet import Fernet

from services import identity_image_checks, verified_reporters


def _encoded_image(width: int = 1000, height: int = 700, noisy: bool = True) -> bytes:
    if noisy:
        pixels = np.random.default_rng(42).integers(
            0, 256, (height, width, 3), dtype=np.uint8
        )
    else:
        pixels = np.zeros((height, width, 3), dtype=np.uint8)
    encoded, buffer = cv2.imencode(".jpg", pixels)
    if not encoded:
        raise RuntimeError("could_not_encode_test_image")
    return buffer.tobytes()


class IdentityImageChecksTests(unittest.TestCase):
    def test_normalizes_syrian_phone_formats(self):
        expected = "+963944123456"
        self.assertEqual(
            identity_image_checks.normalize_syrian_phone("+963 944 123 456"),
            expected,
        )
        self.assertEqual(
            identity_image_checks.normalize_syrian_phone("0944 123 456"),
            expected,
        )
        self.assertEqual(
            identity_image_checks.normalize_syrian_phone("00963 944 123 456"),
            expected,
        )
        self.assertEqual(
            identity_image_checks.normalize_syrian_phone("+٩٦٣ ٩٤٤ ١٢٣ ٤٥٦"),
            expected,
        )

    def test_rejects_non_syrian_or_malformed_phone(self):
        for phone in (
            "+961 944 123 456",
            "0944 123",
            "9441234567",
            "011 123 456",
            "097 123 456",
        ):
            with self.subTest(phone=phone):
                with self.assertRaises(ValueError):
                    identity_image_checks.normalize_syrian_phone(phone)

    def test_rejects_small_and_blurry_images(self):
        with self.assertRaisesRegex(
            identity_image_checks.IdentityImageCheckError,
            "image_dimensions_invalid",
        ):
            identity_image_checks.validate_identity_image(
                "id_front", _encoded_image(400, 300)
            )
        with self.assertRaisesRegex(
            identity_image_checks.IdentityImageCheckError,
            "image_blurry",
        ):
            identity_image_checks.validate_identity_image(
                "id_front", _encoded_image(noisy=False)
            )

    def test_id_image_requires_local_syrian_text_marker(self):
        image = _encoded_image()
        with patch.object(
            identity_image_checks,
            "_read_arabic_text",
            return_value="الجمهوريه العربيه السوريه",
        ):
            identity_image_checks.validate_identity_image("id_front", image)
        with patch.object(
            identity_image_checks,
            "_read_arabic_text",
            return_value="نص غير كاف",
        ):
            with self.assertRaisesRegex(
                identity_image_checks.IdentityImageCheckError,
                "identity_markers_missing",
            ):
                identity_image_checks.validate_identity_image("id_back", image)

    def test_selfie_requires_exactly_one_clear_face(self):
        image = _encoded_image(640, 640)
        with patch.object(
            identity_image_checks,
            "_detect_faces",
            return_value=np.array([[180, 160, 260, 260]]),
        ):
            identity_image_checks.validate_identity_image("selfie", image)
        with patch.object(
            identity_image_checks,
            "_detect_faces",
            return_value=np.empty((0, 4), dtype=int),
        ):
            with self.assertRaisesRegex(
                identity_image_checks.IdentityImageCheckError,
                "face_not_found",
            ):
                identity_image_checks.validate_identity_image("selfie", image)
        with patch.object(
            identity_image_checks,
            "_detect_faces",
            return_value=np.array([[80, 80, 180, 180], [360, 80, 180, 180]]),
        ):
            with self.assertRaisesRegex(
                identity_image_checks.IdentityImageCheckError,
                "face_multiple",
            ):
                identity_image_checks.validate_identity_image("selfie", image)

    def test_encrypted_documents_accept_jpeg_and_png_only(self):
        key = Fernet.generate_key().decode("ascii")
        image = _encoded_image()
        with patch.dict(os.environ, {"VERIFICATION_ENCRYPTION_KEY": key}):
            encrypted = verified_reporters.encrypt_document("id_front", image)
            self.assertEqual(verified_reporters.decrypt_document(encrypted), image)
            with self.assertRaisesRegex(ValueError, "must_be_jpeg_or_png"):
                verified_reporters.encrypt_document("id_front", b"not an image")


if __name__ == "__main__":
    unittest.main()
