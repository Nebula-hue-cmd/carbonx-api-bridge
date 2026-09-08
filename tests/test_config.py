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


class TestEnsureConfig(unittest.TestCase):
    def test_creates_with_random_token_and_mock(self):
        import tempfile

        d = tempfile.mkdtemp()
        path = os.path.join(d, "config.json")
        created, token = config.ensure_config(path)
        self.assertTrue(created)
        self.assertTrue(token and len(token) >= 20)
        loaded = config.load_config(path)
        self.assertEqual(loaded["default_provider"], "mock")
        self.assertIn(token, loaded["auth"]["tokens"])
        self.assertEqual(loaded["auth"]["tokens"][token]["tier"], "admin")
        self.assertIn("cursor", loaded["providers"])
        self.assertEqual(loaded["providers"]["cursor"]["type"], "custom")

    def test_never_overwrites_existing(self):
        import tempfile

        d = tempfile.mkdtemp()
        path = os.path.join(d, "config.json")
        created, _ = config.ensure_config(path)
        self.assertTrue(created)
        with open(path, "r", encoding="utf-8") as fh:
            first = fh.read()
        created2, token2 = config.ensure_config(path)
        self.assertFalse(created2)
        self.assertIsNone(token2)
        with open(path, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), first)

    def test_custom_and_other_types_accepted(self):
        import tempfile

        d = tempfile.mkdtemp()
        path = os.path.join(d, "config.json")
        created, _ = config.ensure_config(path)
        self.assertTrue(created)
        import json

        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        data["providers"]["openaistyle"] = {"type": "custom", "api_style": "openai", "base_url": "http://localhost:8080/v1"}
        data["providers"]["alias"] = {"type": "other", "api_style": "anthropic", "base_url": "http://localhost:3000"}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        loaded = config.load_config(path)
        self.assertIn("openaistyle", loaded["providers"])
        self.assertIn("alias", loaded["providers"])


if __name__ == "__main__":
    unittest.main()