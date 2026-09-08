"""Config loading and validation tests."""

from __future__ import annotations

import os
import unittest

from carbonx_bridge import config

from tests.helpers import TempConfigDir, make_config


class TestLoadConfig(unittest.TestCase):
    def test_loads_and_merges_defaults(self):
        cfg = make_config()
        with TempConfigDir(cfg) as ctx:
            loaded = config.load_config(ctx.path)
        self.assertEqual(loaded["server"]["host"], "127.0.0.1")
        self.assertEqual(loaded["default_provider"], "mock")
        self.assertIn("mock", loaded["providers"])

    def test_requires_at_least_one_provider(self):
        cfg = make_config()
        cfg["providers"] = {}
        with TempConfigDir(cfg) as ctx:
            with self.assertRaisesRegex(ValueError, "at least one provider"):
                config.load_config(ctx.path)

    def test_unknown_provider_type_rejected(self):
        cfg = make_config()
        cfg["providers"]["bogus"] = {"type": "deepseek", "api_key": "k"}
        with TempConfigDir(cfg) as ctx:
            with self.assertRaisesRegex(ValueError, "unknown provider type"):
                config.load_config(ctx.path)

    def test_default_provider_must_exist(self):
        cfg = make_config({"default_provider": "missing"})
        with TempConfigDir(cfg) as ctx:
            with self.assertRaisesRegex(ValueError, "not defined"):
                config.load_config(ctx.path)

    def test_unknown_tier_rejected(self):
        cfg = make_config()
        cfg["auth"]["tokens"] = {"t": {"user": "u", "tier": "golden"}}
        with TempConfigDir(cfg) as ctx:
            with self.assertRaisesRegex(ValueError, "unknown tier"):
                config.load_config(ctx.path)

    def test_env_resolution(self):
        env = {"MY_KEY": "sk-abcdefghijklmnop"}
        self.assertEqual(config.resolve("env:MY_KEY", env), "sk-abcdefghijklmnop")
        self.assertEqual(config.resolve("plain", env), "plain")

    def test_env_missing_raises(self):
        with self.assertRaisesRegex(ValueError, "not set"):
            config.resolve("env:NOPE", {})


if __name__ == "__main__":
    unittest.main()