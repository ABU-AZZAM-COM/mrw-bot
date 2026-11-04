"""مسارات حسابات الإدارة: الدخول، الجلسات، الأدوار، وتدوير الرمز.

انتقلت من ``api/admin_api.py``. تعتمد مباشرة على ``api.admin_auth`` للمصادقة
وكلمات المرور، فلا حاجة لتمريرها، ولا لاستيراد دائري.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from api.admin_auth import (
    ADMIN_MAX_SESSIONS,
    ADMIN_PASSWORD_ITERATIONS,
    ADMIN_PERMISSIONS,
    ADMIN_SESSION_TTL_SECONDS,
    ROLE_PERMISSIONS,
    _admin_account_label,
    _admin_actor,
    _auth,
    _environment_owner_configured,
    _owner_only,
    _password_credentials,
    _password_matches,
    _token_digest,
    expected_token,
    token_ok,
)


def register(app, *, db_of, bad) -> None:
    logger = logging.getLogger(__name__)
    @app.post("/api/admin/login")
    async def admin_login(request: Request, body: dict[str, Any]) -> JSONResponse:
        credential = body.get("credential")
        if (
            not isinstance(credential, str)
            or not credential
            or len(credential) > 256
        ):
            return bad("bad_credentials", 401)

        accounts, bootstrap_disabled = getattr(
            request.state, "admin_auth_snapshot", ([], False)
        )
        expected = expected_token()
        if expected and not bootstrap_disabled and token_ok(credential, expected):
            return JSONResponse({"ok": True, "token": credential})

        raw = os.getenv("ADMIN_API_TOKENS_JSON", "").strip()
        if raw and not bootstrap_disabled:
            try:
                configured_tokens = json.loads(raw)
            except json.JSONDecodeError:
                logger.error("ADMIN_API_TOKENS_JSON is not valid JSON")
                return JSONResponse(
                    {"ok": False, "error": "api_token_configuration_invalid"},
                    status_code=503,
                )
            if not isinstance(configured_tokens, list):
                return JSONResponse(
                    {"ok": False, "error": "api_token_configuration_invalid"},
                    status_code=503,
                )
            if any(
                isinstance(item, dict)
                and isinstance(item.get("token"), str)
                and item.get("role") in ("viewer", "reviewer", "operator", "owner")
                and token_ok(credential, item["token"])
                for item in configured_tokens
            ):
                return JSONResponse({"ok": True, "token": credential})

        credential_hash = _token_digest(credential)
        account = next(
            (
                item
                for item in accounts
                if item.get("active") is True
                and isinstance(item.get("token_hash"), str)
                and secrets.compare_digest(credential_hash, item["token_hash"])
            ),
            None,
        )
        if account is not None:
            return JSONResponse({"ok": True, "token": credential})

        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return bad("unauthorized", 401)
        name = name.strip().casefold()
        account = next(
            (
                item
                for item in accounts
                if item.get("active") is True
                and str(item.get("name") or "").strip().casefold() == name
                and _password_matches(item, credential)
            ),
            None,
        )
        if account is None:
            return bad("unauthorized", 401)

        session_token = secrets.token_urlsafe(32)
        session_hash = _token_digest(session_token)
        now = int(time.time())
        account_id = account.get("id")

        def append_session(current_accounts, _bootstrap_disabled):
            current = next(
                (
                    item
                    for item in current_accounts
                    if item.get("id") == account_id and item.get("active") is True
                ),
                None,
            )
            if current is None or not _password_matches(current, credential):
                return False
            sessions = current.get("sessions")
            if not isinstance(sessions, list):
                sessions = []
            sessions = [
                item
                for item in sessions
                if isinstance(item, dict)
                and isinstance(item.get("expires_at"), int)
                and item["expires_at"] > now
            ]
            sessions.append({
                "token_hash": session_hash,
                "expires_at": now + ADMIN_SESSION_TTL_SECONDS,
            })
            current["sessions"] = sessions[-ADMIN_MAX_SESSIONS:]
            return True

        db = getattr(request.app.state, "admin_db_provider", lambda: None)()
        if db is None:
            return JSONResponse(
                {"ok": False, "error": "admin_auth_unavailable"}, status_code=503
            )
        issued = await asyncio.to_thread(db.mutate_admin_auth_snapshot, append_session)
        if issued is False:
            return bad("unauthorized", 401)
        return JSONResponse({"ok": True, "token": session_token})

    @app.get("/api/admin/session")
    async def admin_session(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        role = getattr(request.state, "admin_role", "owner")
        return JSONResponse({
            "ok": True,
            "role": role,
            "admin_id": _admin_actor(request),
            "permissions": getattr(request.state, "admin_permissions", []),
        })

    @app.get("/api/admin/admins")
    async def list_admins(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        accounts, bootstrap_disabled = request.state.admin_auth_snapshot
        return JSONResponse({
            "ok": True,
            "bootstrap_disabled": bootstrap_disabled,
            "items": [
                {
                    "id": account.get("id"),
                    "name": account.get("name"),
                    "admin_id": account.get("admin_id"),
                    "role": account.get("role"),
                    "permissions": account.get(
                        "permissions", sorted(ROLE_PERMISSIONS.get(account.get("role"), set()))
                    ),
                    "active": account.get("active") is True,
                    "created_at": account.get("created_at"),
                }
                for account in accounts
            ],
        })

    @app.post("/api/admin/admins")
    async def create_admin(request: Request, body: dict[str, Any]) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        name = str(body.get("name") or "").strip()
        role = str(body.get("role") or "")
        admin_id = body.get("admin_id")
        requested_permissions = body.get("permissions")
        if not name or len(name) > 80 or any(ord(char) < 32 for char in name):
            return bad("bad_admin_name")
        if role not in ("viewer", "reviewer", "operator", "owner"):
            return bad("bad_admin_role")
        password = body.get("password")
        if (
            not isinstance(password, str)
            or len(password) < 12
            or len(password) > 128
            or any(ord(char) < 32 for char in password)
        ):
            return bad("bad_admin_password")
        if requested_permissions is None:
            requested_permissions = sorted(ROLE_PERMISSIONS[role])
        if (
            not isinstance(requested_permissions, list)
            or any(
                not isinstance(item, str) or item not in ADMIN_PERMISSIONS
                for item in requested_permissions
            )
            or "read" not in requested_permissions
        ):
            return bad("bad_admin_permissions")
        permissions = sorted(set(requested_permissions))
        if admin_id is not None and (
            not isinstance(admin_id, int)
            or isinstance(admin_id, bool)
            or admin_id <= 0
        ):
            return bad("bad_admin_id")
        db = db_of()
        accounts, _ = request.state.admin_auth_snapshot
        account = {
            "id": uuid.uuid4().hex,
            "name": name,
            "admin_id": admin_id or 0,
            "role": role,
            "permissions": permissions,
            "active": True,
            **_password_credentials(password),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

        create_outcome = "created"

        def append_account(accounts, _bootstrap_disabled):
            nonlocal create_outcome
            if any(
                str(existing.get("name") or "").strip().casefold()
                == name.casefold()
                for existing in accounts
            ):
                create_outcome = "name_exists"
                return False
            accounts.append(account)
            return True

        await asyncio.to_thread(
            db.mutate_admin_auth_snapshot,
            append_account,
            audit_event=(
                _admin_actor(request),
                "admin_created",
                account["id"],
                f"name={name}; role={role}; "
                f"identity_documents="
                f"{'granted' if 'identity_documents' in permissions else 'not_granted'}; "
                f"by={_admin_account_label(request)}",
            ),
        )
        if create_outcome == "name_exists":
            return bad("admin_name_exists", 409)
        return JSONResponse({
            "ok": True,
            "item": {
                "id": account["id"],
                "name": account["name"],
                "admin_id": account["admin_id"],
                "role": account["role"],
                "permissions": account["permissions"],
                "active": True,
                "created_at": account["created_at"],
            },
        })

    @app.put("/api/admin/admins/{account_id}")
    async def update_admin(
        account_id: str, request: Request, body: dict[str, Any]
    ) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        role = str(body.get("role") or "")
        active = body.get("active")
        requested_permissions = body.get("permissions")
        if role not in ("viewer", "reviewer", "operator", "owner"):
            return bad("bad_admin_role")
        if requested_permissions is None:
            requested_permissions = sorted(ROLE_PERMISSIONS[role])
        if (
            not isinstance(requested_permissions, list)
            or any(
                not isinstance(item, str) or item not in ADMIN_PERMISSIONS
                for item in requested_permissions
            )
            or "read" not in requested_permissions
        ):
            return bad("bad_admin_permissions")
        if not isinstance(active, bool):
            return bad("bad_admin_status")
        password = body.get("password")
        if password is not None and (
            not isinstance(password, str)
            or len(password) < 12
            or len(password) > 128
            or any(ord(char) < 32 for char in password)
        ):
            return bad("bad_admin_password")
        db = db_of()
        outcome = "updated"
        snapshot_accounts, _bootstrap_disabled = request.state.admin_auth_snapshot
        existing_account = next(
            (item for item in snapshot_accounts if item.get("id") == account_id),
            None,
        )
        previous_document_access = bool(
            existing_account
            and "identity_documents"
            in (
                existing_account.get("permissions")
                or ROLE_PERMISSIONS.get(existing_account.get("role"), set())
            )
        )
        requested_document_access = "identity_documents" in requested_permissions

        def apply_update(accounts, bootstrap_disabled):
            nonlocal outcome
            account = next(
                (item for item in accounts if item.get("id") == account_id),
                None,
            )
            if account is None:
                outcome = "missing"
                return False
            if account.get("role") == "owner" and account.get("active") is True:
                remaining_owners = sum(
                    1
                    for item in accounts
                    if item.get("role") == "owner"
                    and item.get("active") is True
                    and item.get("id") != account_id
                )
                bootstrap_enabled = (
                    not bootstrap_disabled and _environment_owner_configured()
                )
                if (
                    (role != "owner" or not active)
                    and not remaining_owners
                    and not bootstrap_enabled
                ):
                    outcome = "last_owner"
                    return False
            account["role"] = role
            account["permissions"] = sorted(set(requested_permissions))
            account["active"] = active
            if password is not None:
                account.update(_password_credentials(password))
                account.pop("token_hash", None)
                account["sessions"] = []
            elif not active:
                account["sessions"] = []
            return True

        await asyncio.to_thread(
            db.mutate_admin_auth_snapshot,
            apply_update,
            audit_event=(
                _admin_actor(request),
                "admin_updated",
                account_id,
                f"role={role}; active={active}; "
                f"password_changed={password is not None}; "
                f"by={_admin_account_label(request)}",
            ),
        )
        if outcome == "missing":
            return bad("admin_not_found", 404)
        if outcome == "last_owner":
            return bad("last_owner_cannot_be_removed", 409)
        if (
            outcome == "updated"
            and existing_account is not None
            and previous_document_access != requested_document_access
        ):
            await db.audit(
                _admin_actor(request),
                (
                    "admin_identity_documents_granted"
                    if requested_document_access
                    else "admin_identity_documents_revoked"
                ),
                account_id,
                f"by={_admin_account_label(request)}",
            )
        return JSONResponse({"ok": True})

    @app.post("/api/admin/admin-token/rotate")
    async def rotate_admin_token(request: Request) -> JSONResponse:
        refuse = _auth(request)
        if refuse:
            return refuse
        refuse = _owner_only(request)
        if refuse:
            return refuse
        db = db_of()
        token = secrets.token_urlsafe(32)
        account = {
            "id": uuid.uuid4().hex,
            "name": "المالك الرئيسي",
            "admin_id": _admin_actor(request),
            "role": "owner",
            "active": True,
            "token_hash": _token_digest(token),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

        def rotate_accounts(accounts, _bootstrap_disabled):
            for existing in accounts:
                if existing.get("role") == "owner":
                    existing["active"] = False
            accounts.append(account)
            return True

        await asyncio.to_thread(
            db.mutate_admin_auth_snapshot,
            rotate_accounts,
            disable_bootstrap=True,
            audit_event=(
                _admin_actor(request),
                "admin_token_rotated",
                account["id"],
                f"by={_admin_account_label(request)}",
            ),
        )
        return JSONResponse({"ok": True, "token": token})

