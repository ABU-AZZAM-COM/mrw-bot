"""Read-only status connectors for public project integrations."""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

_CACHE_TTL_SECONDS = 60
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, Any] | None = None
_CACHE_AT = 0.0


def _github_repository() -> str:
    value = os.getenv("GITHUB_REPOSITORY", "ABU-AZZAM-COM/mrw-bot").strip()
    owner, separator, name = value.partition("/")
    if not separator or not owner or not name or "/" in name:
        return "ABU-AZZAM-COM/mrw-bot"
    return value


def _fetch_json(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "mrw-bot-read-only-monitor",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=4) as response:
        return json.loads(response.read(1_000_000).decode("utf-8"))


def _run_status(run: dict[str, Any]) -> str:
    if run.get("status") != "completed":
        return "running"
    conclusion = str(run.get("conclusion") or "").lower()
    return "healthy" if conclusion in {"success", "neutral", "skipped"} else "degraded"


def _clean_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(run.get("name") or run.get("display_title") or "فحص آلي")[:160],
        "status": _run_status(run),
        "conclusion": str(run.get("conclusion") or "")[:40],
        "event": str(run.get("event") or "")[:40],
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "url": str(run.get("html_url") or "")[:500],
    }


def _clean_pull_request(pull: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": int(pull.get("number") or 0),
        "title": str(pull.get("title") or "دمج تغييرات")[:200],
        "merged_at": pull.get("merged_at"),
        "url": str(pull.get("html_url") or "")[:500],
    }


def _load_activity(fetch_json: Callable[[str], Any]) -> dict[str, Any]:
    repository = _github_repository()
    base = f"https://api.github.com/repos/{repository}"
    try:
        runs_data = fetch_json(
            f"{base}/actions/runs?per_page=10"
        )
        pulls_data = fetch_json(
            f"{base}/pulls?state=closed&sort=updated&direction=desc&per_page=20"
        )
        runs_raw = runs_data.get("workflow_runs", []) if isinstance(runs_data, dict) else []
        pulls_raw = pulls_data if isinstance(pulls_data, list) else []
        runs = [
            _clean_run(run) for run in runs_raw if isinstance(run, dict)
        ][:10]
        merged = [
            _clean_pull_request(pull)
            for pull in pulls_raw
            if isinstance(pull, dict) and pull.get("merged_at")
        ][:10]
        latest_run = runs[0] if runs else None
        state = (
            latest_run["status"]
            if latest_run is not None
            else "unknown"
        )
        return {
            "status": state,
            "repository": repository,
            "recent_runs": runs,
            "recent_merges": merged,
            "error": None,
        }
    except (OSError, TimeoutError, urllib.error.URLError, ValueError, TypeError):
        return {
            "status": "unknown",
            "repository": repository,
            "recent_runs": [],
            "recent_merges": [],
            "error": "github_unavailable",
        }


def github_activity(
    fetch_json: Callable[[str], Any] | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Return cached public workflow and merge activity without requiring a token."""
    global _CACHE, _CACHE_AT
    now = time.monotonic()
    with _CACHE_LOCK:
        if (
            not force_refresh
            and _CACHE is not None
            and now - _CACHE_AT < _CACHE_TTL_SECONDS
        ):
            return dict(_CACHE)
        result = _load_activity(fetch_json or _fetch_json)
        result["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _CACHE = result
        _CACHE_AT = now
        return dict(result)
