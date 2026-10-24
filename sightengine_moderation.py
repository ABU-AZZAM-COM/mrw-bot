"""Sightengine API client and nudity-score parsing."""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SIGHTENGINE_URL = "https://api.sightengine.com/1.0/check.json"
EXPLICIT_THRESHOLD = 0.70
REQUEST_TIMEOUT_SECONDS = 25.0


class SightengineError(RuntimeError):
    """The external moderation service could not return a usable result."""


@dataclass(frozen=True)
class NudityScores:
    raw: float
    sexual_activity: float

    @property
    def is_explicit(self) -> bool:
        return self.raw > EXPLICIT_THRESHOLD or self.sexual_activity > EXPLICIT_THRESHOLD


def credentials() -> tuple[str, str] | None:
    api_user = os.getenv("SIGHTENGINE_API_USER", "").strip()
    api_secret = (
        os.getenv("SIGHTENGINE_API_SECRET", "")
        or os.getenv("SIGHTENGINE_SECRET", "")
    ).strip()
    if not api_user or not api_secret:
        return None
    return api_user, api_secret


def parse_scores(payload: Any) -> NudityScores:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise SightengineError("Sightengine returned an unsuccessful response")
    nudity = payload.get("nudity")
    if not isinstance(nudity, dict):
        raise SightengineError("Sightengine response has no nudity scores")

    values: list[float] = []
    for key in ("raw", "sexual_activity"):
        value = nudity.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SightengineError(f"Invalid Sightengine score: {key}")
        score = float(value)
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise SightengineError(f"Invalid Sightengine score: {key}")
        values.append(score)
    return NudityScores(raw=values[0], sexual_activity=values[1])


async def inspect_image(path: str | Path) -> NudityScores:
    auth = credentials()
    if auth is None:
        raise SightengineError("Sightengine credentials are not configured")

    api_user, api_secret = auth
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            with Path(path).open("rb") as image:
                response = await client.post(
                    SIGHTENGINE_URL,
                    data={
                        "api_user": api_user,
                        "api_secret": api_secret,
                        "models": "nudity-2.0",
                    },
                    files={"media": (Path(path).name, image, "application/octet-stream")},
                )
            response.raise_for_status()
            return parse_scores(response.json())
    except SightengineError:
        raise
    except (httpx.HTTPError, OSError, ValueError) as exc:
        raise SightengineError("Sightengine request or response failed") from exc
