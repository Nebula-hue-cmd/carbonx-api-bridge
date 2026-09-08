"""Tests for the local control panel (/ and /ui/* endpoints)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from carbonx_bridge import config as cfg_mod
from carbonx_bridge.server import create_server
from tests.helpers import RunningServer, make_config, write_json


def _make_ui_server(overrides=None):
    cfg = make_config(overrides)
    tmp = tempfile.TemporaryDirectory(prefix="cui-")
    path = os.path.join(tmp.name, "config.json")
    write_json(path, cfg)
    server = RunningServer(cfg, config_path=path)
    return server, tmp


class TestControlPanel(unittest.TestCase):
    def test_page_served(self):
        server, tmp = _make_ui_server()
        try:
            status, headers, data = server.request("/")
            self.assertEqual(status, 200)
            self.assertTrue(headers.get("Content-Type", "").startswith("text/html"))
            self.assertIn(b"CarbonX API Bridge", data)
        finally:
            server.close()
            tmp.cleanup()

    def test_ui_endpoints_loopback_only(self):
        server, tmp = _make_ui_server()
        try:
            with mock.patch("carbonx_bridge.server.ui.is_loopback", return_value=False):
                status, _ = server.json("/ui/token")
                self.assertEqual(status, 403)
                status, _ = server.json("/ui/config")
                self.assertEqual(status, 403)
                status, _, _ = server.request("/")
                self.assertEqual(status, 200)  # the page itself is public
        finally:
            server.close()
            tmp.cleanup()


class TestTokens(unittest.TestCase):
    def test_lists_tokens(self):
        server, tmp = _make_ui_server()
        try:
            status, body = server.json("/ui/token")
            self.assertEqual(status, 200)
            self.assertEqual(body["tokens"][0]["token"], "test-token-admin")
            self.assertEqual(body["tokens"][0]["tier"], "admin")
        finally:
            server.close()
            tmp.cleanup()

    def test_new_token_works_immediately(self):
        server, tmp = _make_ui_server()
        try:
            status, body = server.json("/ui/token", body={})
            self.assertEqual(status, 200)
            new = body["token"]
            self.assertTrue(new and len(new) >= 20)
            # new token authenticates without any restart
            status, chat = server.json("/v1/chat", body={"messages": [{"role": "user", "content": "hi"}]}, token=new)
            self.assertEqual(status, 200)
            self.assertEqual(chat["content"], "hello from mock")
            # and it appears in the config file on disk (auth.tokens)
            with open(os.path.join(tmp.name, "config.json"), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertIn(new, data["auth"]["tokens"])
        finally:
            server.close()
            tmp.cleanup()


class TestConfigEditor(unittest.TestCase):
    def test_config_view_is_sanitized(self):
        cfg = make_config(
            {
                "providers": {
                    "openai": {"type": "openai", "api_key": "sk-topsecret-do-not-leak", "default_model": "gpt-4o-mini"}
                }
            }
        )
        server, tmp = _make_ui_server(cfg)
        try:
            status, body = server.json("/ui/config")
            self.assertEqual(status, 200)
            self.assertEqual(body["default_provider"], "mock")
            self.assertTrue(body["providers"]["openai"]["has_key"])
            text = json.dumps(body)
            self.assertNotIn("sk-topsecret-do-not-leak", text)
            self.assertNotIn("api_key", text)  # the VIEW uses has_key only
        finally:
            server.close()
            tmp.cleanup()

    def test_save_api_key_no_restart(self):
        server, tmp = _make_ui_server()
        try:
            status, body = server.json("/ui/config", body={"providers": {"mock": {"api_key": "sk-just-typed"}}})
            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            # persisted to disk
            with open(os.path.join(tmp.name, "config.json"), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data["providers"]["mock"]["api_key"], "sk-just-typed")
            # never echoed back
            status, view = server.json("/ui/config")
            self.assertEqual(status, 200)
            self.assertNotIn("sk-just-typed", json.dumps(view))
            # server still healthy
            status, _ = server.json("/health")
            self.assertEqual(status, 200)
        finally:
            server.close()
            tmp.cleanup()

    def test_empty_key_preserves_existing(self):
        server, tmp = _make_ui_server()
        try:
            server.json("/ui/config", body={"providers": {"mock": {"api_key": "sk-first"}}})
            server.json("/ui/config", body={"providers": {"mock": {"api_key": "   "}}})
            with open(os.path.join(tmp.name, "config.json"), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data["providers"]["mock"]["api_key"], "sk-first")
        finally:
            server.close()
            tmp.cleanup()

    def test_invalid_default_provider_returns_400(self):
        server, tmp = _make_ui_server()
        try:
            status, body = server.json("/ui/config", body={"default_provider": "nope"})
            self.assertEqual(status, 400)
            self.assertIn("type", body.get("error", {}))
            # server is fine afterwards
            status, _ = server.json("/health")
            self.assertEqual(status, 200)
        finally:
            server.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()