"""المصادقة والصلاحيات لواجهة الإدارة.

كانت هذه الوحدة جزءًا من ``api/admin_api.py``؛ فُصلت لأن المصادقة مسؤولية
مستقلة عن تعريف المسارات، ولتسهيل اختبارها ومراجعتها بمعزل عن بقية الملف.
كل الرموز هنا مستوردة في ``admin_api`` بنفس الأسماء، فلا يتغير سلوكها.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
from typing import Any

from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

ADMIN_PERMISSIONS = {
    "read", "review", "users", "operate", "export", "delete", "identity",
    "identity_documents", "identity_delete", "contact_users",
}
ROLE_PERMISSIONS = {
    "viewer": {"read"},
    "reviewer": {"read", "review"},
    "operator": {"read", "review", "users", "operate", "export"},
    "owner": ADMIN_PERMISSIONS - {"identity_documents", "identity_delete"},
}
FAR_FUTURE = "9999-12-31T00:00:00"
ADMIN_PASSWORD_ITERATIONS = 600_000
ADMIN_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
ADMIN_MAX_SESSIONS = 8

# ==================================================================
# الحماية بالتوكن
# ==================================================================
def expected_token() -> str:
    """سرّ الواجهة من بيئة المساحة (فارغ = المسارات مغلقة)."""
    return os.getenv("ADMIN_API_TOKEN", "").strip()


def _environment_owner_configured() -> bool:
    if expected_token():
        return True
    raw = os.getenv("ADMIN_API_TOKENS_JSON", "").strip()
    if not raw:
        return False
    try:
        records = json.loads(raw)
    except json.JSONDecodeError:
        return False
    return isinstance(records, list) and any(
        isinstance(entry, dict)
        and entry.get("role") == "owner"
        and isinstance(entry.get("token"), str)
        and bool(entry["token"].strip())
        for entry in records
    )


def token_ok(got: str, expected: str) -> bool:
    """مقارنة زمنية ثابتة أن التوكن المُرسل مطابق للسرّ."""
    import hmac

    got, expected = (got or "").strip(), (expected or "").strip()
    if not got or not expected:
        return False
    return hmac.compare_digest(got, expected)


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _password_credentials(password: str) -> dict[str, Any]:
    salt = secrets.token_bytes(16)
    return {
        "password_salt": salt.hex(),
        "password_hash": hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, ADMIN_PASSWORD_ITERATIONS
        ).hex(),
        "password_iterations": ADMIN_PASSWORD_ITERATIONS,
    }


def _password_matches(account: dict[str, Any], password: str) -> bool:
    salt_hex = account.get("password_salt")
    digest = account.get("password_hash")
    iterations = account.get("password_iterations")
    if (
        not isinstance(salt_hex, str)
        or not isinstance(digest, str)
        or not isinstance(iterations, int)
        or isinstance(iterations, bool)
        or not ADMIN_PASSWORD_ITERATIONS <= iterations <= 1_000_000
    ):
        return False
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    if len(salt) != 16:
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    ).hex()
    import hmac

    return hmac.compare_digest(actual, digest)


def _auth(request):
    """Authenticate the request and enforce the server-side token role."""
    app = getattr(request, "app", None)
    db_provider = getattr(getattr(app, "state", None), "admin_db_provider", None)
    persisted_accounts: list[dict[str, Any]] = []
    bootstrap_disabled = False
    snapshot = getattr(
        getattr(request, "state", None), "admin_auth_snapshot", None
    )
    if snapshot is not None:
        persisted_accounts, bootstrap_disabled = snapshot
    elif getattr(getattr(request, "state", None), "admin_auth_unavailable", False):
        return JSONResponse(
            {"ok": False, "error": "admin_auth_unavailable"},
            status_code=503,
        )
    elif db_provider is not None:
        try:
            persisted_accounts, bootstrap_disabled = (
                db_provider().admin_auth_snapshot()
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not load persisted admin credentials")
            return JSONResponse(
                {"ok": False, "error": "admin_auth_unavailable"},
                status_code=503,
            )
    expected = expected_token()
    configured: list[dict[str, Any]] = []
    raw = os.getenv("ADMIN_API_TOKENS_JSON", "").strip()
    if raw and not bootstrap_disabled:
        try:
            records = json.loads(raw)
        except json.JSONDecodeError:
            logger.error("ADMIN_API_TOKENS_JSON is not valid JSON")
            return JSONResponse(
                {"ok": False, "error": "api_token_configuration_invalid"},
                status_code=503,
            )
        if not isinstance(records, list):
            return JSONResponse(
                {"ok": False, "error": "api_token_configuration_invalid"},
                status_code=503,
            )
        for entry in records:
            if not isinstance(entry, dict):
                continue
            token = entry.get("token")
            role = str(entry.get("role") or "")
            if not isinstance(token, str) or not token.strip():
                continue
            if role not in ("viewer", "reviewer", "operator", "owner"):
                continue
            admin_id = entry.get("admin_id", 0)
            if not isinstance(admin_id, int) or isinstance(admin_id, bool):
                admin_id = 0
            configured.append({
                "token": token.strip(),
                "role": role,
                "admin_id": admin_id,
            })
    if expected and not bootstrap_disabled:
        configured.append({"token": expected, "role": "owner", "admin_id": 0})
    configured.extend(
        {
            "token_hash": account.get("token_hash"),
            "role": account.get("role"),
            "permissions": account.get("permissions"),
            "admin_id": account.get("admin_id", 0),
            "account_id": account.get("id"),
        }
        for account in persisted_accounts
        if account.get("active") is True
        and account.get("role") in ("viewer", "reviewer", "operator", "owner")
        and isinstance(account.get("token_hash"), str)
    )
    now = int(time.time())
    for account in persisted_accounts:
        if (
            account.get("active") is not True
            or account.get("role") not in ("viewer", "reviewer", "operator", "owner")
        ):
            continue
        sessions = account.get("sessions")
        if not isinstance(sessions, list):
            continue
        for session in sessions:
            if (
                isinstance(session, dict)
                and isinstance(session.get("token_hash"), str)
                and isinstance(session.get("expires_at"), int)
                and session["expires_at"] > now
            ):
                configured.append({
                    "token_hash": session["token_hash"],
                    "role": account["role"],
                    "permissions": account.get("permissions"),
                    "admin_id": account.get("admin_id", 0),
                    "account_id": account.get("id"),
                    "session": True,
                })
    if not configured and not expected and not raw:
        return JSONResponse(
            {"ok": False, "error": "api_token_not_configured"}, status_code=503
        )
    path = getattr(getattr(request, "url", None), "path", "")
    authorization = request.headers.get("authorization") or ""
    bearer_token = (
        authorization[7:].strip()
        if authorization[:7].lower() == "bearer "
        else ""
    )
    file_endpoint = (
        path.startswith("/api/admin/photo/")
        or path.startswith("/api/admin/verified-reporters/")
        or path.startswith("/api/admin/export/")
        or path == "/api/admin/backup/download"
    )
    got = bearer_token if file_endpoint else (
        bearer_token or request.headers.get("x-admin-token") or ""
    )
    token_hash = _token_digest(got) if got else ""
    principal = next(
        (
            item
            for item in configured
            if (
                token_ok(got, item["token"])
                if "token" in item
                else secrets.compare_digest(
                    token_hash, str(item.get("token_hash") or "")
                )
            )
        ),
        None,
    )
    if principal is None:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    method = getattr(request, "method", "POST").upper()
    if (
        path == "/api/admin/contact-settings"
        or path.startswith("/api/admin/conversations")
        or (
            path.startswith("/api/admin/users/")
            and any(
                marker in path
                for marker in ("/messages", "/message", "/media", "/contact")
            )
        )
    ):
        permission = "contact_users"
    elif path.startswith("/api/admin/verified-reporters/") and path.endswith("/delete"):
        permission = "identity_delete"
    elif path.startswith("/api/admin/verified-reporters") and (
        "/documents/" in path or path.endswith("/pdf")
    ):
        permission = "identity_documents"
    elif path.startswith("/api/admin/verified-reporters"):
        permission = "identity"
    elif path.startswith("/api/admin/notifications") or path == "/api/admin/events/stream":
        permission = "read"
    elif path.startswith("/api/admin/export/") or path.startswith("/api/admin/backup/"):
        permission = "export"
    elif method in ("GET", "HEAD", "OPTIONS"):
        permission = "read"
    elif method == "DELETE" and path.startswith("/api/admin/reports/"):
        permission = "delete"
    elif path.endswith(("/status", "/type", "/note", "/reply", "/workflow", "/followup")):
        permission = "review"
    elif path.startswith("/api/admin/users/"):
        permission = "users"
    else:
        permission = "operate"

    configured_permissions = principal.get("permissions")
    if configured_permissions is None:
        allowed_permissions = ROLE_PERMISSIONS[principal["role"]]
    elif (
        isinstance(configured_permissions, list)
        and all(
            isinstance(item, str) and item in ADMIN_PERMISSIONS
            for item in configured_permissions
        )
    ):
        allowed_permissions = set(configured_permissions)
    else:
        allowed_permissions = set()
    if principal["role"] == "owner":
        allowed_permissions = set(ROLE_PERMISSIONS["owner"])
        if isinstance(configured_permissions, list) and (
            "identity_documents" in configured_permissions
        ):
            allowed_permissions.add("identity_documents")
        if isinstance(configured_permissions, list) and (
            "identity_delete" in configured_permissions
        ):
            allowed_permissions.add("identity_delete")
    if permission not in allowed_permissions:
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    try:
        request.state.admin_role = principal["role"]
        request.state.admin_id = principal["admin_id"]
        request.state.admin_permissions = sorted(allowed_permissions)
        request.state.admin_account_id = principal.get("account_id")
        request.state.admin_session_hash = (
            token_hash if principal.get("session") else None
        )
    except AttributeError:
        pass
    return None


def _admin_actor(request) -> int:
    return int(getattr(getattr(request, "state", None), "admin_id", 0) or 0)


def _admin_account_label(request) -> str:
    account_id = getattr(getattr(request, "state", None), "admin_account_id", None)
    return f"account={account_id}" if account_id else "environment-token"


def _owner_only(request):
    if getattr(getattr(request, "state", None), "admin_role", "") != "owner":
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    return None


