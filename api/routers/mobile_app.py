"""Public mobile-app policy and owner-managed Android release settings."""
from __future__ import annotations

import ipaddress
import json
import re
import urllib.parse
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from api.admin_auth import _admin_actor, _auth, _owner_only

POLICY_KEY = "mobile_app_policy"
DEFAULT_POLICY: dict[str, str] = {
    "status": "active",
    "status_message": "",
    "latest_version": "",
    "update_mode": "optional",
    "apk_url": "",
    "sha256": "",
    "release_notes": "",
}


async def load_mobile_policy(db: Any) -> dict[str, str]:
    raw = await db.get_state(POLICY_KEY)
    if not raw:
        return dict(DEFAULT_POLICY)
    saved = json.loads(raw)
    if not isinstance(saved, dict):
        raise ValueError("Invalid persisted mobile app policy")
    return {**DEFAULT_POLICY, **saved}


def _validated_policy(body: Any) -> dict[str, str] | None:
    if not isinstance(body, dict):
        return None

    status = body.get("status")
    update_mode = body.get("update_mode")
    version = body.get("latest_version", "")
    apk_url = body.get("apk_url", "")
    sha256 = body.get("sha256", "")
    status_message = body.get("status_message", "")
    release_notes = body.get("release_notes", "")
    if (
        not isinstance(status, str)
        or status not in {"active", "paused", "disabled"}
        or not isinstance(update_mode, str)
        or update_mode not in {"optional", "required"}
        or not isinstance(version, str)
        or not isinstance(apk_url, str)
        or not isinstance(sha256, str)
        or not isinstance(status_message, str)
        or not isinstance(release_notes, str)
    ):
        return None

    version = version.strip()
    apk_url = apk_url.strip()
    sha256 = sha256.strip().lower()
    status_message = status_message.strip()
    release_notes = release_notes.strip()
    if version and (
        len(version) > 32 or re.fullmatch(r"\d+(?:\.\d+){1,3}", version) is None
    ):
        return None
    if apk_url:
        try:
            parsed = urllib.parse.urlsplit(apk_url)
            host = parsed.hostname or ""
            _ = parsed.port
            try:
                address = ipaddress.ip_address(host) if host else None
            except ValueError:
                address = None
            if (
                parsed.scheme.lower() != "https"
                or not host
                or parsed.username
                or parsed.password
                or host.lower() == "localhost"
                or host.lower().endswith((".localhost", ".local"))
                or (address is not None and not address.is_global)
                or len(apk_url) > 2048
            ):
                return None
        except ValueError:
            return None
    if sha256 and re.fullmatch(r"[a-f0-9]{64}", sha256) is None:
        return None
    if bool(version) != bool(apk_url) or bool(apk_url) != bool(sha256):
        return None
    if len(status_message) > 1000 or len(release_notes) > 4000:
        return None
    if update_mode == "required" and not version:
        return None

    return {
        "status": status,
        "status_message": status_message,
        "latest_version": version,
        "update_mode": update_mode,
        "apk_url": apk_url,
        "sha256": sha256,
        "release_notes": release_notes,
    }


def register(app, *, db_of, bad) -> None:
    @app.get("/api/mobile/policy")
    async def mobile_policy() -> JSONResponse:
        return JSONResponse({"ok": True, **await load_mobile_policy(db_of())})

    @app.get("/api/admin/mobile-app")
    async def mobile_app_settings_get(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        return JSONResponse({
            "ok": True,
            "policy": await load_mobile_policy(db_of()),
        })

    @app.put("/api/admin/mobile-app")
    async def mobile_app_settings_put(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        try:
            body = await request.json()
        except ValueError:
            return bad("invalid_json")
        policy = _validated_policy(body)
        if policy is None:
            return bad("invalid_mobile_app_policy")
        db = db_of()
        await db.set_state(POLICY_KEY, json.dumps(policy, ensure_ascii=False))
        await db.audit(
            _admin_actor(request),
            "mobile_app_policy_changed",
            policy["status"],
            f"version={policy['latest_version']} mode={policy['update_mode']}",
        )
        return JSONResponse({"ok": True, "policy": policy})
