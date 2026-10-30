import hashlib
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

os.environ.setdefault("BOT_TOKEN", "123:TEST_TOKEN")

from api.admin_api import _auth, register
from database import Database


class FakeDB:
    def __init__(self, accounts=None, bootstrap_disabled=False):
        self.accounts = accounts or []
        self.bootstrap_disabled = bootstrap_disabled
        self.audit_entries = []
        self.fail_audit = False

    def admin_auth_snapshot(self):
        return self.accounts, self.bootstrap_disabled

    def save_admin_auth_snapshot(self, accounts, *, disable_bootstrap=False):
        self.accounts = accounts
        if disable_bootstrap:
            self.bootstrap_disabled = True

    def mutate_admin_auth_snapshot(
        self, mutation, *, disable_bootstrap=False, audit_event=None
    ):
        accounts = [dict(account) for account in self.accounts]
        result = mutation(accounts, self.bootstrap_disabled)
        if result is not False:
            if audit_event is not None:
                if self.fail_audit:
                    raise RuntimeError("simulated audit failure")
                self.audit_entries.append(audit_event)
            self.save_admin_auth_snapshot(
                accounts,
                disable_bootstrap=disable_bootstrap,
            )
        return result

    async def audit(self, actor_id, action, target=None, details=None):
        self.audit_entries.append((actor_id, action, target, details))


class AdminManagementTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.app = FastAPI()
        register(self.app)
        bot_main = types.ModuleType("app.main")
        bot_main.app = types.SimpleNamespace(bot_data={"db": self.db})
        self.modules_patch = patch.dict(sys.modules, {"app.main": bot_main})
        self.modules_patch.start()
        self.env_patch = patch.dict(os.environ, {
            "ADMIN_API_TOKEN": "bootstrap-owner-token",
            "ADMIN_API_TOKENS_JSON": "",
        })
        self.env_patch.start()
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.env_patch.stop()
        self.modules_patch.stop()

    def test_login_preserves_environment_token_access(self):
        response = self.client.post(
            "/api/admin/login",
            json={"credential": "bootstrap-owner-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["token"], "bootstrap-owner-token")

    def test_create_admin_stores_password_hash_and_login_issues_session(self):
        response = self.client.post(
            "/api/admin/admins",
            headers={"Authorization": "Bearer bootstrap-owner-token"},
            json={
                "name": "مشرف المراجعة",
                "role": "reviewer",
                "admin_id": 1234,
                "password": "strong-admin-password-2026",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("token", response.json())
        self.assertNotIn("password_hash", response.json()["item"])
        self.assertNotIn("password_salt", response.json()["item"])
        self.assertEqual(self.db.audit_entries[0][1], "admin_created")
        account = self.db.accounts[0]
        self.assertEqual(account["password_iterations"], 600_000)
        self.assertNotEqual(account["password_hash"], "strong-admin-password-2026")
        self.assertEqual(account["role"], "reviewer")
        self.assertEqual(account["permissions"], ["read", "review"])
        self.assertEqual(account["admin_id"], 1234)

        failed_login = self.client.post(
            "/api/admin/login",
            json={"name": "مشرف المراجعة", "credential": "wrong-password"},
        )
        self.assertEqual(failed_login.status_code, 401)
        login = self.client.post(
            "/api/admin/login",
            json={
                "name": "مشرف المراجعة",
                "credential": "strong-admin-password-2026",
            },
        )
        self.assertEqual(login.status_code, 200)
        token = login.json()["token"]
        self.assertNotIn(token, repr(self.db.accounts))
        self.assertEqual(len(self.db.accounts[0]["sessions"]), 1)

        session = self.client.get(
            "/api/admin/session", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(session.status_code, 200)
        self.assertEqual(session.json()["role"], "reviewer")
        self.assertEqual(
            self.client.get(
                "/api/admin/admins",
                headers={"Authorization": f"Bearer {token}"},
            ).status_code,
            403,
        )

    def test_create_admin_accepts_explicit_permission_subset(self):
        response = self.client.post(
            "/api/admin/admins",
            headers={"Authorization": "Bearer bootstrap-owner-token"},
            json={
                "name": "مشرف التصدير",
                "role": "viewer",
                "permissions": ["read", "export"],
                "password": "another-strong-password",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.db.accounts[0]["permissions"],
            ["export", "read"],
        )

    def test_create_admin_rejects_short_password(self):
        response = self.client.post(
            "/api/admin/admins",
            headers={"Authorization": f"Bearer {os.environ['ADMIN_API_TOKEN']}"},
            json={
                "name": "مشرف بكلمة ضعيفة",
                "role": "reviewer",
                "password": "short",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "bad_admin_password")
        self.assertEqual(self.db.accounts, [])

    def test_owner_password_reset_revokes_existing_admin_sessions(self):
        created = self.client.post(
            "/api/admin/admins",
            headers={"Authorization": f"Bearer {os.environ['ADMIN_API_TOKEN']}"},
            json={
                "name": "مشرف كلمات المرور",
                "role": "reviewer",
                "password": "initial-admin-password",
            },
        )
        self.assertEqual(created.status_code, 200)
        account_id = created.json()["item"]["id"]
        login = self.client.post(
            "/api/admin/login",
            json={
                "name": "مشرف كلمات المرور",
                "credential": "initial-admin-password",
            },
        )
        old_session = login.json()["token"]

        updated = self.client.put(
            f"/api/admin/admins/{account_id}",
            headers={"Authorization": f"Bearer {os.environ['ADMIN_API_TOKEN']}"},
            json={
                "role": "reviewer",
                "permissions": ["read", "review"],
                "active": True,
                "password": "replacement-admin-password",
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(
            self.client.get(
                "/api/admin/session",
                headers={"Authorization": f"Bearer {old_session}"},
            ).status_code,
            401,
        )
        replacement_login = self.client.post(
            "/api/admin/login",
            json={
                "name": "مشرف كلمات المرور",
                "credential": "replacement-admin-password",
            },
        )
        self.assertEqual(replacement_login.status_code, 200)

    def test_selected_permission_groups_are_enforced_server_side(self):
        token = "limited-admin-token"
        account = {
            "id": "admin-1",
            "role": "viewer",
            "permissions": ["read", "export"],
            "active": True,
            "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        }
        request = Request({
            "type": "http",
            "app": self.app,
            "method": "POST",
            "path": "/api/admin/reports/123/status",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
            "http_version": "1.1",
        })
        request.state.admin_auth_snapshot = ([account], True)

        self.assertEqual(_auth(request).status_code, 403)

        account["permissions"] = ["read", "review"]
        self.assertIsNone(_auth(request))

        for method, path, required_permission in (
            ("GET", "/api/admin/verified-reporters", "identity"),
            (
                "GET",
                "/api/admin/verified-reporters/1/documents/selfie",
                "identity_documents",
            ),
            ("GET", "/api/admin/verified-reporters/1/pdf", "identity_documents"),
            (
                "POST",
                "/api/admin/verified-reporters/1/decision",
                "identity",
            ),
        ):
            identity_request = Request({
                "type": "http",
                "app": self.app,
                "method": method,
                "path": path,
                "headers": request.scope["headers"],
                "query_string": b"",
                "scheme": "http",
                "server": ("test", 80),
                "client": ("test", 1),
                "http_version": "1.1",
            })
            identity_request.state.admin_auth_snapshot = ([account], True)
            account["permissions"] = ["read"]
            self.assertEqual(_auth(identity_request).status_code, 403, path)

            account["permissions"] = ["read", required_permission]
            self.assertIsNone(_auth(identity_request), path)

        account["role"] = "owner"
        account.pop("permissions")
        document_request = Request({
            "type": "http",
            "app": self.app,
            "method": "GET",
            "path": "/api/admin/verified-reporters/1/documents/selfie",
            "headers": request.scope["headers"],
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
            "http_version": "1.1",
        })
        document_request.state.admin_auth_snapshot = ([account], True)
        self.assertEqual(_auth(document_request).status_code, 403)
        account["permissions"] = ["read", "identity", "identity_documents"]
        self.assertIsNone(_auth(document_request))

    def test_rotating_owner_token_disables_old_environment_tokens(self):
        response = self.client.post(
            "/api/admin/admin-token/rotate",
            headers={"Authorization": "Bearer bootstrap-owner-token"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.db.bootstrap_disabled)
        new_token = response.json()["token"]
        self.assertEqual(self.db.audit_entries[0][1], "admin_token_rotated")
        self.assertEqual(
            self.client.get(
                "/api/admin/session",
                headers={"Authorization": f"Bearer {new_token}"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                "/api/admin/session",
                headers={"Authorization": "Bearer bootstrap-owner-token"},
            ).status_code,
            401,
        )

    def test_owner_token_rotation_keeps_old_access_if_audit_fails(self):
        token = "bootstrap-owner-token"
        self.assertEqual(
            self.client.get(
                "/api/admin/session",
                headers={"Authorization": f"Bearer {token}"},
            ).status_code,
            200,
        )
        self.db.fail_audit = True
        with TestClient(self.app, raise_server_exceptions=False) as client:
            response = client.post(
                "/api/admin/admin-token/rotate",
                headers={"Authorization": f"Bearer {token}"},
            )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.db.accounts, [])
        self.assertFalse(self.db.bootstrap_disabled)
        self.assertEqual(
            self.client.get(
                "/api/admin/session",
                headers={"Authorization": f"Bearer {token}"},
            ).status_code,
            200,
        )

    def test_last_active_owner_cannot_be_disabled(self):
        token = "persisted-owner-token"
        self.db.accounts = [{
            "id": "owner-1",
            "name": "مالك",
            "admin_id": 0,
            "role": "owner",
            "active": True,
            "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        }]
        self.db.bootstrap_disabled = True

        response = self.client.put(
            "/api/admin/admins/owner-1",
            headers={"Authorization": f"Bearer {token}"},
            json={"role": "owner", "active": False},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "last_owner_cannot_be_removed")
        self.assertTrue(self.db.accounts[0]["active"])

    def test_database_admin_snapshot_survives_reopen(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "admin.sqlite"
            db = Database(path, backend="sqlite")
            account = {"id": "account-1", "role": "owner", "active": True}
            db.save_admin_auth_snapshot([account], disable_bootstrap=True)
            db._conn.close()

            reopened = Database(path, backend="sqlite")
            self.assertEqual(
                reopened.admin_auth_snapshot(),
                ([account], True),
            )
            reopened._conn.close()

    def test_database_serializes_concurrent_admin_updates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "admin.sqlite", backend="sqlite")

            def add_account(index):
                db.mutate_admin_auth_snapshot(
                    lambda accounts, _: accounts.append({"id": str(index)})
                )

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(add_account, range(24)))

            self.assertEqual(len(db.admin_auth_snapshot()[0]), 24)
            db._conn.close()

    def test_admin_auth_update_rolls_back_if_audit_insert_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "admin.sqlite", backend="sqlite")
            owner = {"id": "owner-1", "role": "owner", "active": True}
            db.save_admin_auth_snapshot([owner])
            db._conn.execute(
                """
                CREATE TRIGGER reject_admin_rotation_audit
                BEFORE INSERT ON audit_log
                WHEN NEW.action = 'admin_token_rotated'
                BEGIN
                    SELECT RAISE(ABORT, 'simulated audit failure');
                END
                """
            )
            db._conn.commit()

            def rotate(accounts, _bootstrap_disabled):
                accounts[0]["active"] = False
                accounts.append({"id": "owner-2", "role": "owner", "active": True})
                return True

            with self.assertRaises(sqlite3.IntegrityError):
                db.mutate_admin_auth_snapshot(
                    rotate,
                    disable_bootstrap=True,
                    audit_event=(1, "admin_token_rotated", "owner-2", None),
                )

            self.assertEqual(db.admin_auth_snapshot(), ([owner], False))
            db._conn.close()


if __name__ == "__main__":
    unittest.main()
