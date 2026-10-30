"""Validation and matching helpers for the admin-managed TXT word list."""
from __future__ import annotations

import re
import unicodedata

MAX_WORD_LIST_BYTES = 100 * 1024
MAX_WORDS = 2000
MAX_TERM_LENGTH = 100


def normalize_term(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = normalized.replace("\u0640", "")
    normalized = "".join(
        char for char in normalized
        if unicodedata.category(char) not in {"Mn", "Me"}
    )
    return re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE).strip()


def parse_word_list(content: bytes) -> list[str]:
    if not isinstance(content, bytes) or len(content) > MAX_WORD_LIST_BYTES:
        raise ValueError("wordlist_too_large")
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("wordlist_invalid_encoding") from exc

    terms: list[str] = []
    seen: set[str] = set()
    for line in decoded.splitlines():
        term = line.strip()
        if not term or term.startswith("#"):
            continue
        if (
            len(term) > MAX_TERM_LENGTH
            or any(ord(char) < 32 for char in term)
            or not normalize_term(term)
        ):
            raise ValueError("wordlist_invalid_line")
        key = normalize_term(term)
        if key not in seen:
            terms.append(term)
            seen.add(key)
        if len(terms) > MAX_WORDS:
            raise ValueError("wordlist_too_many_terms")
    if not terms:
        raise ValueError("wordlist_empty")
    return terms


def contains_blocked_term(text: str, terms: list[str]) -> bool:
    normalized_text = normalize_term(text)
    if not normalized_text:
        return False
    padded_text = f" {normalized_text} "
    return any(
        (normalized_term := normalize_term(term))
        and f" {normalized_term} " in padded_text
        for term in terms
    )
