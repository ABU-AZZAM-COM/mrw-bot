"""Local-only preliminary checks for verified-reporter identity images."""
from __future__ import annotations

import re
import unicodedata
from io import BytesIO

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000
MIN_DOCUMENT_WIDTH = 800
MIN_DOCUMENT_HEIGHT = 500
MIN_SELFIE_WIDTH = 640
MIN_SELFIE_HEIGHT = 640


class IdentityImageCheckError(ValueError):
    """A local check rejected an image or could not run safely."""


_SYRIAN_ID_MARKERS = {
    "id_front": (
        "الجمهورية العربية السورية",
        "وزارة الداخلية",
        "بطاقة شخصية",
        "الرقم الوطني",
        "سوريا",
        "سورية",
    ),
    "id_back": (
        "الجمهورية العربية السورية",
        "الرقم الوطني",
        "رقم وطني",
        "مكان القيد",
        "تاريخ الولادة",
        "مكان الولادة",
        "السجل المدني",
        "الجنسية",
        "سورية",
    ),
}


def normalize_syrian_phone(raw_phone: str) -> str:
    """Return a Syrian number in E.164 form, rejecting other country codes."""
    if not isinstance(raw_phone, str):
        raise ValueError("invalid_syrian_phone")
    digits = "".join(
        str(unicodedata.digit(char))
        for char in raw_phone
        if char.isdigit()
    )
    if digits.startswith("00963"):
        national = digits[5:]
    elif digits.startswith("963"):
        national = digits[3:]
    elif digits.startswith("0"):
        if len(digits) != 10:
            raise ValueError("invalid_syrian_phone")
        national = digits[1:]
    else:
        national = digits

    if len(national) != 9 or not national.startswith(
        ("93", "94", "95", "96", "98", "99")
    ):
        raise ValueError("invalid_syrian_phone")
    return f"+963{national}"


def _image_media_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return None


def _normalize_arabic_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = "".join(
        char for char in normalized
        if not unicodedata.combining(char)
    )
    normalized = normalized.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    normalized = normalized.translate(
        str.maketrans({"ة": "ه", "ى": "ي", "ؤ": "و", "ئ": "ي"})
    )
    normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
    return re.sub(r"\s+", " ", normalized).strip()


def _detect_faces(image):
    import cv2

    cascade_path = f"{cv2.data.haarcascades}haarcascade_frontalface_default.xml"
    cascade = cv2.CascadeClassifier(cascade_path)
    if cascade.empty():
        raise IdentityImageCheckError("inspection_unavailable")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cascade.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(70, 70),
    )


def _read_arabic_text(image) -> str:
    import cv2
    import pytesseract

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return pytesseract.image_to_string(
        gray,
        lang="ara+eng",
        config="--psm 6",
        timeout=12,
    )


def validate_identity_image(kind: str, data: bytes) -> None:
    """Raise a coded error unless this image passes local preliminary checks."""
    if kind not in {"selfie", "id_front", "id_back"}:
        raise ValueError("invalid_verification_document_kind")
    if not isinstance(data, bytes) or not data or len(data) > MAX_IMAGE_BYTES:
        raise IdentityImageCheckError("image_size_invalid")
    if _image_media_type(data) is None:
        raise IdentityImageCheckError("image_format_invalid")

    try:
        from PIL import Image
        import cv2
        import numpy as np

        with Image.open(BytesIO(data)) as decoded:
            if decoded.format not in {"JPEG", "PNG"}:
                raise IdentityImageCheckError("image_format_invalid")
            width, height = decoded.size
            if width * height > MAX_IMAGE_PIXELS:
                raise IdentityImageCheckError("image_dimensions_invalid")
            decoded.verify()
        image = cv2.imdecode(
            np.frombuffer(data, dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
    except IdentityImageCheckError:
        raise
    except Exception as exc:
        raise IdentityImageCheckError("image_unreadable") from exc

    if image is None:
        raise IdentityImageCheckError("image_unreadable")
    height, width = image.shape[:2]
    if kind == "selfie":
        if width < MIN_SELFIE_WIDTH or height < MIN_SELFIE_HEIGHT:
            raise IdentityImageCheckError("image_dimensions_invalid")
    elif min(width, height) < MIN_DOCUMENT_HEIGHT or max(width, height) < MIN_DOCUMENT_WIDTH:
        raise IdentityImageCheckError("image_dimensions_invalid")

    try:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    except Exception as exc:
        raise IdentityImageCheckError("inspection_unavailable") from exc
    if sharpness < (45 if kind == "selfie" else 30):
        raise IdentityImageCheckError("image_blurry")

    if kind == "selfie":
        try:
            faces = _detect_faces(image)
        except IdentityImageCheckError:
            raise
        except Exception as exc:
            raise IdentityImageCheckError("inspection_unavailable") from exc
        if len(faces) == 0:
            raise IdentityImageCheckError("face_not_found")
        if len(faces) != 1:
            raise IdentityImageCheckError("face_multiple")
        x, y, face_width, face_height = (int(value) for value in faces[0])
        if (
            face_width < width * 0.12
            or face_height < height * 0.12
            or x < 0
            or y < 0
            or x + face_width > width
            or y + face_height > height
        ):
            raise IdentityImageCheckError("face_unclear")
        return

    try:
        text = _normalize_arabic_text(_read_arabic_text(image))
    except IdentityImageCheckError:
        raise
    except Exception as exc:
        raise IdentityImageCheckError("inspection_unavailable") from exc
    markers = tuple(
        _normalize_arabic_text(marker)
        for marker in _SYRIAN_ID_MARKERS[kind]
    )
    if not any(marker in text for marker in markers):
        raise IdentityImageCheckError("identity_markers_missing")
