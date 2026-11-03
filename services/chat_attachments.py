"""Validation helpers for files attached to user-administration conversations."""
from __future__ import annotations

import os
import re
import zipfile
import zlib
from io import BytesIO
from pathlib import PurePosixPath
from xml.etree import ElementTree

from PIL import Image, UnidentifiedImageError

MAX_CHAT_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000

_IMAGE_TYPES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "GIF": "image/gif",
    "WEBP": "image/webp",
}
_OFFICE_TYPES = {
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


def sanitize_attachment_filename(value: str | None) -> str:
    filename = str(value or "attachment").replace("\\", "/")
    filename = PurePosixPath(filename).name
    filename = re.sub(r"[\x00-\x1f\x7f]", "", filename).strip()
    return filename[:180] or "attachment"


def validate_chat_attachment(
    data: bytes, filename: str
) -> tuple[str, str] | None:
    """Return media kind and verified content type, or None for unsupported files."""
    if not data or len(data) > MAX_CHAT_ATTACHMENT_BYTES:
        return None

    lower_name = filename.lower()
    try:
        with Image.open(BytesIO(data)) as image:
            content_type = _IMAGE_TYPES.get(str(image.format or ""))
            expected_suffixes = {
                "image/png": {".png"},
                "image/jpeg": {".jpg", ".jpeg"},
                "image/gif": {".gif"},
                "image/webp": {".webp"},
            }
            if (
                content_type
                and os.path.splitext(lower_name)[1]
                in expected_suffixes.get(content_type, set())
                and image.width > 0
                and image.height > 0
                and image.width * image.height <= MAX_IMAGE_PIXELS
            ):
                image.verify()
                with Image.open(BytesIO(data)) as decoded:
                    decoded.load()
                return ("image", content_type)
    except (
        Image.DecompressionBombError,
        OSError,
        UnidentifiedImageError,
        ValueError,
    ):
        pass
    if data.startswith(b"%PDF-"):
        if os.path.splitext(lower_name)[1] != ".pdf":
            return None
        if b"%%EOF" not in data[-2048:]:
            return None
        return ("document", "application/pdf")

    spec = _OFFICE_TYPES.get(os.path.splitext(lower_name)[1])
    if spec is None or not data.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return None

    required_part, expected_part_type, content_type = spec
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 4096 or sum(item.file_size for item in entries) > 64 * 1024 * 1024:
                return None
            names = {item.filename for item in entries}
            if (
                "[Content_Types].xml" not in names
                or required_part not in names
                or any(name.lower().endswith("vbaproject.bin") for name in names)
            ):
                return None
            info = archive.getinfo("[Content_Types].xml")
            if info.file_size > 256 * 1024:
                return None
            root = ElementTree.fromstring(archive.read(info))
            matching_types = {
                item.attrib.get("ContentType")
                for item in root
                if item.attrib.get("PartName", "").lstrip("/") == required_part
            }
            if expected_part_type not in matching_types:
                return None
            required_info = archive.getinfo(required_part)
            if required_info.file_size > 8 * 1024 * 1024:
                return None
            ElementTree.fromstring(archive.read(required_info))
    except (
        OSError,
        ValueError,
        KeyError,
        RuntimeError,
        NotImplementedError,
        EOFError,
        zlib.error,
        zipfile.BadZipFile,
        ElementTree.ParseError,
    ):
        return None
    return ("document", content_type)
