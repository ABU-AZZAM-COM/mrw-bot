"""Encryption helpers for optional verified-reporter identity submissions."""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

from services.identity_image_checks import MAX_IMAGE_BYTES

MAX_CAPTURE_BYTES = MAX_IMAGE_BYTES
DOCUMENT_KINDS = {"selfie", "id_front", "id_back"}


class VerificationUnavailable(RuntimeError):
    """Raised when identity storage cannot be encrypted with the configured key."""


def _fernet() -> Fernet:
    key = os.getenv("VERIFICATION_ENCRYPTION_KEY", "").strip()
    if not key:
        raise VerificationUnavailable(
            "verification_encryption_key_not_configured"
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise VerificationUnavailable(
            "verification_encryption_key_invalid"
        ) from exc


def is_configured() -> bool:
    try:
        _fernet()
    except VerificationUnavailable:
        return False
    return True


def encrypt(value: bytes) -> bytes:
    return _fernet().encrypt(value)


def decrypt(value: bytes) -> bytes:
    try:
        return _fernet().decrypt(value)
    except InvalidToken as exc:
        raise VerificationUnavailable(
            "verification_document_decryption_failed"
        ) from exc


def encrypt_phone(phone: str) -> bytes:
    return encrypt(phone.encode("utf-8"))


def encrypt_captures(captures: dict[str, bytes]) -> dict[str, bytes]:
    if set(captures) != DOCUMENT_KINDS:
        raise ValueError("verification_documents_incomplete")
    return {
        kind: encrypt_document(kind, data)
        for kind, data in captures.items()
    }


def encrypt_document(kind: str, data: bytes) -> bytes:
    if kind not in DOCUMENT_KINDS:
        raise ValueError("invalid_verification_document_kind")
    if not isinstance(data, bytes) or not data or len(data) > MAX_CAPTURE_BYTES:
        raise ValueError("verification_document_invalid_size")
    if image_media_type(data) is None:
        raise ValueError("verification_document_must_be_jpeg_or_png")
    return encrypt(data)


def image_media_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return None


def decrypt_document(value: bytes) -> bytes:
    data = decrypt(value)
    if image_media_type(data) is None:
        raise VerificationUnavailable("verification_document_corrupt")
    return data


def hash_ticket(ticket: str) -> str:
    import hashlib

    return hashlib.sha256(ticket.encode("utf-8")).hexdigest()
