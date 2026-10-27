import hashlib
import os
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

    def admin_auth_snapshot(self):
        return self.accounts, self.bootstrap_disabled

    def save_admin_auth_snapshot(self, accounts, *, disable_bootstrap=False):
        self.accounts = accounts
        if disable_bootstrap:
            self.bootstrap_disabled = True

    def mutate_admin_auth_snapshot(
        self, mutation, *, disable_bootstrap=False
    ):
        result = mutation(self.accounts, self.bootstrap_disabled)
        if result is not False:
            self.save_admin_auth_snapshot(
                self.accounts,
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

    def test_create_admin_returns_token_once_and_persists_only_digest(self):
        response = self.client.post(
            "/api/admin/admins",
            headers={"Authorization": "Bearer bootstrap-owner-token"},
            json={"name": "مشرف المراجعة", "role": "reviewer", "admin_id": 1234},
        )

        self.assertEqual(response.status_code, 200)
        token = response.json()["token"]
        self.assertTrue(token)
        self.assertNotIn(token, repr(self.db.accounts))
        account = self.db.accounts[0]
        self.assertEqual(account["token_hash"], hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(account["role"], "reviewer")
        self.assertEqual(account["permissions"], ["read", "review"])
        self.assertEqual(account["admin_id"], 1234)

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
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.db.accounts[0]["permissions"],
            ["export", "read"],
        )

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

    def test_rotating_owner_token_disables_old_environment_tokens(self):
        response = self.client.post(
            "/api/admin/admin-token/rotate",
            headers={"Authorization": "Bearer bootstrap-owner-token"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.db.bootstrap_disabled)
        new_token = response.json()["token"]
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


if __name__ == "__main__":
    unittest.main()
