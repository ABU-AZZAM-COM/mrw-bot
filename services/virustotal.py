"""VirusTotal URL submissions for user-provided complaint links."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass

import httpx

from app import config

logger = logging.getLogger(__name__)

BASE_URL = "https://www.virustotal.com/api/v3"
REQUEST_TIMEOUT_SECONDS = 8.0
CACHE_TTL_SECONDS = 6 * 60 * 60
PENDING_CACHE_TTL_SECONDS = 2 * 60


@dataclass(frozen=True)
class ScanResult:
    status: str
    malicious: int = 0
    suspicious: int = 0

    @property
    def needs_review(self) -> bool:
        return self.status != "completed" or self.malicious > 0 or self.suspicious > 0


_cache: dict[str, tuple[float, ScanResult]] = {}
_cache_lock = asyncio.Lock()


def _parse_analysis(payload: object) -> ScanResult:
    if not isinstance(payload, dict):
        raise ValueError("invalid VirusTotal response")
    data = payload.get("data")
    attributes = data.get("attributes") if isinstance(data, dict) else None
    if not isinstance(attributes, dict):
        raise ValueError("missing VirusTotal analysis attributes")
    status = attributes.get("status")
    if status != "completed":
        return ScanResult(status="pending")
    stats = attributes.get("stats")
    if not isinstance(stats, dict):
        raise ValueError("missing VirusTotal analysis statistics")
    malicious = stats.get("malicious", 0)
    suspicious = stats.get("suspicious", 0)
    if not isinstance(malicious, int) or not isinstance(suspicious, int):
        raise ValueError("invalid VirusTotal analysis statistics")
    return ScanResult(status="completed", malicious=malicious, suspicious=suspicious)


async def scan_url(url: str) -> ScanResult:
    """Submit a URL without fetching it locally; failures require manual review."""
    api_key = config.VIRUSTOTAL_API_KEY
    if not api_key:
        return ScanResult(status="not_configured")

    cache_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    async with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        if cached:
            _cache.pop(cache_key, None)

        headers = {"x-apikey": api_key}
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                submitted = await client.post(
                    f"{BASE_URL}/urls", headers=headers, data={"url": url}
                )
                submitted.raise_for_status()
                payload = submitted.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                analysis_id = data.get("id") if isinstance(data, dict) else None
                if not isinstance(analysis_id, str) or not analysis_id:
                    raise ValueError("missing VirusTotal analysis id")

                analysis = await client.get(
                    f"{BASE_URL}/analyses/{analysis_id}", headers=headers
                )
                analysis.raise_for_status()
                result = _parse_analysis(analysis.json())
        except (httpx.HTTPError, ValueError, TypeError):
            logger.warning("VirusTotal URL scan failed; sending report to manual review")
            return ScanResult(status="unavailable")

        ttl = (
            CACHE_TTL_SECONDS
            if result.status == "completed"
            else PENDING_CACHE_TTL_SECONDS
        )
        _cache[cache_key] = (time.monotonic() + ttl, result)
        return result
