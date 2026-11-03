import asyncio
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.admin_api import register
from database import Database
from services.report_taxonomy import REPORT_CATEGORIES


class ReportTaxonomyApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(
            Path(self.temp_dir.name) / "taxonomy.sqlite",
            backend="sqlite",
        )
        self.app = FastAPI()
        register(self.app)
        bot_main = types.ModuleType("app.main")
        bot_main.app = SimpleNamespace(bot_data={"db": self.db})
        self.modules_patch = patch.dict(sys.modules, {"app.main": bot_main})
        self.modules_patch.start()
        self.env_patch = patch.dict(os.environ, {
            "ADMIN_API_TOKEN": "bootstrap-owner-token",
            "ADMIN_API_TOKENS_JSON": "",
        })
        self.env_patch.start()
        self.client = TestClient(self.app)
        self.headers = {"Authorization": "Bearer bootstrap-owner-token"}

    def tearDown(self):
        self.client.close()
        self.env_patch.stop()
        self.modules_patch.stop()
        self.db.close()
        self.temp_dir.cleanup()

    def test_grouped_types_are_read_and_saved(self):
        loaded = self.client.get("/api/admin/vtypes", headers=self.headers)
        self.assertEqual(loaded.status_code, 200)
        items = loaded.json()["items"]
        groups = {category: [] for category in REPORT_CATEGORIES}
        categories = list(REPORT_CATEGORIES)
        for index, item in enumerate(items):
            groups[categories[index % len(categories)]].append(item)

        updated = self.client.put(
            "/api/admin/vtypes",
            headers=self.headers,
            json={"items": items, "groups": groups},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["groups"], groups)
        self.assertEqual(
            asyncio.run(self.db.effective_vtype_groups()),
            groups,
        )

    def test_unassigned_type_is_rejected(self):
        loaded = self.client.get("/api/admin/vtypes", headers=self.headers)
        self.assertEqual(loaded.status_code, 200)
        items = loaded.json()["items"]
        groups = {
            category: [items[0]]
            for category in REPORT_CATEGORIES
        }
        response = self.client.put(
            "/api/admin/vtypes",
            headers=self.headers,
            json={"items": items, "groups": groups},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "unassigned_types")


if __name__ == "__main__":
    unittest.main()
