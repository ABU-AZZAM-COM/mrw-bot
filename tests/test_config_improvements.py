import importlib
import os
import sys
import unittest
from pathlib import Path


class ConfigImprovementsTests(unittest.TestCase):
    def setUp(self):
        os.environ["BOT_TOKEN"] = "123:TEST_TOKEN"
        os.environ["ADMIN_IDS"] = "1001, 2002;3003, bad"
        os.environ["ALLOW_GROUP_REPORTS"] = "true"
        os.environ["SAVE_MEDIA_LOCALLY"] = "0"
        sys.modules.pop("config", None)
        import config
        self.config = importlib.reload(config)

    def tearDown(self):
        sys.modules.pop("config", None)

    def test_parse_admin_ids_handles_common_separators(self):
        self.assertEqual(self.config.parse_admin_ids("1001, 2002;3003, bad"), (1001, 2002, 3003))

    def test_parse_bool_handles_common_truthy_and_falsey_values(self):
        self.assertTrue(self.config.parse_bool("true"))
        self.assertTrue(self.config.parse_bool("1"))
        self.assertFalse(self.config.parse_bool("false"))
        self.assertFalse(self.config.parse_bool("0"))
        self.assertFalse(self.config.parse_bool("no"))

    def test_runtime_config_uses_parsed_values(self):
        self.assertTrue(self.config.ALLOW_GROUP_REPORTS)
        self.assertFalse(self.config.SAVE_MEDIA_LOCALLY)
        self.assertEqual(self.config.ADMIN_IDS, (1001, 2002, 3003))

    def test_root_compatibility_import_uses_package_module(self):
        import app.config as package_config

        self.assertIs(self.config, package_config)
        self.assertEqual(
            self.config.BASE_DIR,
            Path(__file__).resolve().parents[1],
        )


if __name__ == "__main__":
    unittest.main()
