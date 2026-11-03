import tempfile
import unittest
from pathlib import Path

from database import Database
from services.report_taxonomy import (
    REPORT_CATEGORIES,
    default_vtype_groups,
    normalize_vtype_groups,
)


class ReportTaxonomyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(
            Path(self.temp_dir.name) / "taxonomy.sqlite",
            backend="sqlite",
        )

    async def asyncTearDown(self):
        self.db._conn.close()
        self.temp_dir.cleanup()

    async def test_default_groups_keep_existing_types_available(self):
        items = await self.db.effective_vtypes()
        groups = await self.db.effective_vtype_groups()
        self.assertEqual(groups, default_vtype_groups(items))
        self.assertEqual(set(groups), set(REPORT_CATEGORIES))
        self.assertEqual(len(items), 24)
        self.assertTrue(all(groups.values()))
        self.assertTrue(all(values != items for values in groups.values()))

    async def test_legacy_defaults_migrate_to_category_specific_defaults(self):
        from services.report_taxonomy import LEGACY_VIOLATION_TYPES

        await self.db.set_vtypes_override(list(LEGACY_VIOLATION_TYPES))
        await self.db.set_vtype_groups(
            {
                category: list(LEGACY_VIOLATION_TYPES)
                for category in REPORT_CATEGORIES
            }
        )

        items = await self.db.effective_vtypes()
        groups = await self.db.effective_vtype_groups()

        self.assertEqual(len(items), 24)
        self.assertEqual(groups, default_vtype_groups(items))
        self.assertNotEqual(groups["conduct"], groups["social"])

    async def test_group_assignments_are_persisted(self):
        items = await self.db.effective_vtypes()
        groups = default_vtype_groups(items)
        groups["conduct"] = items[:2]
        groups["administrative"] = items[2:4]
        groups["social"] = items[4:6]
        groups["field"] = items[6:]

        await self.db.set_vtype_groups(groups)

        self.assertEqual(await self.db.effective_vtype_groups(), groups)

    def test_normalization_rejects_malformed_group_shapes(self):
        self.assertIsNone(normalize_vtype_groups([], ["نوع"]))
        self.assertIsNone(
            normalize_vtype_groups({"conduct": ["نوع"]}, ["نوع"])
        )


if __name__ == "__main__":
    unittest.main()
