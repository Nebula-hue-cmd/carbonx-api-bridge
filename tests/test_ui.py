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


def _raw_request(server, method, path, headers, body=b""):
    """Send a request with full control over headers (e.g. a forged Host)."""
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", server.server.server_address[1], timeout=15)
    conn.putrequest(method, path, skip_host=("Host" in headers))
    for k, v in headers.items():
        conn.putheader(k, v)
    conn.endheaders(body)
    res = conn.getresponse()
    data = res.read()
    conn.close()
    return res.status, res.headers, data


class TestAdminSurfaceGuards(unittest.TestCase):
    def test_page_rejects_dns_rebinding_host(self):
        server, tmp = _make_ui_server()
        try:
            status, _, _ = _raw_request(server, "GET", "/", {"Host": "rebind.attacker.example"})
            self.assertEqual(status, 403)
            status, _, _ = _raw_request(server, "GET", "/ui/token", {"Host": "127.1.1.1"})
            self.assertEqual(status, 403)
            status, _, _ = _raw_request(server, "GET", "/ui/status", {"Host": "rebind.attacker.example"})
            self.assertEqual(status, 403)
        finally:
            server.close()
            tmp.cleanup()

    def test_admin_endpoints_accept_loopback_hosts(self):
        server, tmp = _make_ui_server()
        try:
            for host in ("127.0.0.1:%d" % server.server.server_address[1], "localhost", "[::1]:%d" % server.server.server_address[1]):
                status, _, _ = _raw_request(server, "GET", "/ui/token", {"Host": host})
                self.assertEqual(status, 200, host)
                status, _, _ = _raw_request(server, "GET", "/ui/status", {"Host": host})
                self.assertEqual(status, 200, host)
        finally:
            server.close()
            tmp.cleanup()

    def test_csrf_blocked_by_fetch_metadata_and_origin(self):
        server, tmp = _make_ui_server()
        try:
            status, body = server.json("/ui/config", headers={"Sec-Fetch-Site": "cross-site"})
            self.assertEqual(status, 403)
            status, body = server.json("/ui/config", headers={"Origin": "https://evil.example"})
            self.assertEqual(status, 403)
            # a normal loopback client still works when the browser marks it same-origin
            status, body = server.json("/ui/config", headers={"Sec-Fetch-Site": "same-origin"})
            self.assertEqual(status, 200)
        finally:
            server.close()
            tmp.cleanup()

    def test_local_extension_origins_tolerated(self):
        # Installed add-ons (Merlin etc.) can only navigate the panel with an
        # extension scheme Origin -- forgeable by nobody but an installed
        # extension process -- so those are let through even when the browser
        # labels them cross-site. Real remote origins stay blocked.
        server, tmp = _make_ui_server()
        try:
            host = "127.0.0.1:%d" % server.server.server_address[1]
            for origin in (
                "chrome-extension://abcdefghijklmnop",
                "edge-extension://abcdefghijklmnop",
                "ms-browser-extension://abcdefghijklmnop",
                "moz-extension://abcdefghijklmnop",
                "safari-web-extension://abcdefghijklmnop",
            ):
                status, _, _ = _raw_request(
                    server,
                    "GET",
                    "/ui/token",
                    {"Host": host, "Origin": origin, "Sec-Fetch-Site": "cross-site"},
                )
                self.assertEqual(status, 200, origin)
            status, _, _ = _raw_request(
                server, "GET", "/ui/token",
                {"Host": host, "Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
            )
            self.assertEqual(status, 403)
            status, _, _ = _raw_request(server, "GET", "/ui/token", {"Host": host, "Sec-Fetch-Site": "cross-site"})
            self.assertEqual(status, 403)
        finally:
            server.close()
            tmp.cleanup()

    def test_api_surface_not_host_gated(self):
        # LAN clients on /v1/* keep working; only the admin surface is rebound-locked
        server, tmp = _make_ui_server()
        try:
            status, chat = server.json(
                "/v1/chat",
                body={"messages": [{"role": "user", "content": "hi"}]},
                token="test-token-admin",
                headers={"Host": "192.168.1.50"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(chat["content"], "hello from mock")
        finally:
            server.close()
            tmp.cleanup()


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
                self.assertEqual(status, 403)  # the whole admin surface is loopback-only
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


class TestTokenDeletion(unittest.TestCase):
    def test_delete_second_token_persists(self):
        server, tmp = _make_ui_server(
            {
                "auth": {
                    "tokens": {
                        "t-one": {"user": "alice", "tier": "admin"},
                        "t-two": {"user": "bob", "tier": "free"},
                    }
                }
            }
        )
        try:
            status, body = server.json("/ui/token", body={"action": "delete", "token": "t-two"})
            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            status, body = server.json("/ui/token")
            self.assertEqual(status, 200)
            remaining = sorted(t["token"] for t in body["tokens"])
            self.assertEqual(remaining, ["t-one", "test-token-admin"])
            with open(os.path.join(tmp.name, "config.json"), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertNotIn("t-two", data["auth"]["tokens"])
        finally:
            server.close()
            tmp.cleanup()

    def test_cannot_delete_last_token(self):
        server, tmp = _make_ui_server()
        try:
            status, body = server.json("/ui/token", body={"action": "delete", "token": "test-token-admin"})
            self.assertEqual(status, 400)
            self.assertIn("last token", body["error"]["message"])
            status, body = server.json("/ui/token")
            self.assertEqual(status, 200)
            self.assertEqual(len(body["tokens"]), 1)
        finally:
            server.close()
            tmp.cleanup()

    def test_delete_unknown_token(self):
        server, tmp = _make_ui_server()
        try:
            status, body = server.json("/ui/token", body={"action": "delete", "token": "nope"})
            self.assertEqual(status, 400)
            self.assertIn("no such token", body["error"]["message"])
            status, _ = server.json("/health")
            self.assertEqual(status, 200)
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

    def test_add_provider_happy_path(self):
        server, tmp = _make_ui_server()
        try:
            status, body = server.json(
                "/ui/config",
                body={
                    "default_provider": "ollama",
                    "providers": {
                        "ollama": {
                            "type": "custom",
                            "api_style": "openai",
                            "base_url": "http://localhost:11434/v1",
                            "default_model": "llama3.1",
                        }
                    },
                },
            )
            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            # live app now serves it as the default provider
            status, health = server.json("/health")
            self.assertEqual(status, 200)
            self.assertIn("ollama", health["providers"])
            self.assertEqual(health["default_provider"], "ollama")
            # persisted to disk and the view reflects it
            with open(os.path.join(tmp.name, "config.json"), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            provider = data["providers"]["ollama"]
            self.assertEqual(provider["type"], "custom")
            self.assertEqual(provider["base_url"], "http://localhost:11434/v1")
            status, view = server.json("/ui/config")
            self.assertEqual(status, 200)
            self.assertEqual(view["default_provider"], "ollama")
            self.assertEqual(view["providers"]["ollama"]["base_url"], "http://localhost:11434/v1")
        finally:
            server.close()
            tmp.cleanup()

    def test_add_provider_bad_name_400(self):
        server, tmp = _make_ui_server()
        try:
            for bad in ("has space", "UPPER#", "", "a" * 33, "x/y"):
                status, body = server.json(
                    "/ui/config", body={"providers": {bad: {"type": "custom", "base_url": "http://localhost:1/v1"}}}
                )
                self.assertEqual(status, 400, bad)
                self.assertEqual(body["error"]["type"], "bad_request")
            status, _ = server.json("/health")
            self.assertEqual(status, 200)
        finally:
            server.close()
            tmp.cleanup()

    def test_add_provider_unknown_type_400(self):
        server, tmp = _make_ui_server()
        try:
            status, body = server.json(
                "/ui/config", body={"providers": {"x": {"type": "wat", "base_url": "http://localhost:1/v1"}}}
            )
            self.assertEqual(status, 400)
            self.assertIn("unknown provider type", body["error"]["message"])
        finally:
            server.close()
            tmp.cleanup()

    def test_add_provider_needs_base_url(self):
        server, tmp = _make_ui_server()
        try:
            for ptype in ("custom", "other", "opencode"):
                status, body = server.json(
                    "/ui/config", body={"providers": {"x": {"type": ptype, "default_model": "m"}}}
                )
                self.assertEqual(status, 400, ptype)
                self.assertIn("base_url", body["error"]["message"])
            # opencode also needs a session even with a base_url
            status, body = server.json(
                "/ui/config", body={"providers": {"x": {"type": "opencode", "base_url": "http://localhost:1"}}}
            )
            self.assertEqual(status, 400)
            self.assertIn("x_opencode_session", body["error"]["message"])
        finally:
            server.close()
            tmp.cleanup()

    def test_add_provider_ignores_unknown_fields(self):
        server, tmp = _make_ui_server()
        try:
            status, body = server.json(
                "/ui/config",
                body={
                    "providers": {
                        "mine": {
                            "type": "custom",
                            "base_url": "http://localhost:99/v1",
                            "exec": "malicious",
                            "__class__": "oops",
                        }
                    }
                },
            )
            self.assertEqual(status, 200)
            with open(os.path.join(tmp.name, "config.json"), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertNotIn("exec", data["providers"]["mine"])
            self.assertNotIn("__class__", data["providers"]["mine"])
        finally:
            server.close()
            tmp.cleanup()

    def test_failed_save_leaves_disk_unouched(self):
        server, tmp = _make_ui_server()
        try:
            before = open(os.path.join(tmp.name, "config.json"), "r", encoding="utf-8").read()
            status, _ = server.json(
                "/ui/config", body={"providers": {"bad name": {"type": "custom", "base_url": "http://localhost:1/v1"}}}
            )
            self.assertEqual(status, 400)
            after = open(os.path.join(tmp.name, "config.json"), "r", encoding="utf-8").read()
            self.assertEqual(after, before)
            # no temp droppings from the failed persist
            leftovers = [f for f in os.listdir(tmp.name) if f.endswith(".tmp")]
            self.assertEqual(leftovers, [])
        finally:
            server.close()
            tmp.cleanup()


class TestSystemPromptPanel(unittest.TestCase):
    def test_view_reports_prompt_state(self):
        server, tmp = _make_ui_server({"server": {"system_prompt": "CUSTOM IDENTITY"}})
        try:
            status, view = server.json("/ui/config")
            self.assertEqual(status, 200)
            self.assertEqual(view["system_prompt"], "CUSTOM IDENTITY")
            self.assertEqual(view["system_prompt_default"], cfg_mod.DEFAULT_SYSTEM_PROMPT)
            self.assertTrue(view["system_prompt_custom"])
        finally:
            server.close()
            tmp.cleanup()

    def test_save_system_prompt_persists_and_reinjects(self):
        server, tmp = _make_ui_server()
        try:
            status, body = server.json("/ui/config", body={"server": {"system_prompt": "MY NEW PROMPT"}})
            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            with open(os.path.join(tmp.name, "config.json"), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data["server"]["system_prompt"], "MY NEW PROMPT")
            status, view = server.json("/ui/config")
            self.assertEqual(status, 200)
            self.assertEqual(view["system_prompt"], "MY NEW PROMPT")
            self.assertTrue(view["system_prompt_custom"])
        finally:
            server.close()
            tmp.cleanup()

    def test_empty_system_prompt_disables_injection(self):
        server, tmp = _make_ui_server({"server": {"system_prompt": "SOMETHING"}})
        try:
            status, body = server.json("/ui/config", body={"server": {"system_prompt": ""}})
            self.assertEqual(status, 200)
            status, view = server.json("/ui/config")
            self.assertEqual(view["system_prompt"], "")
            self.assertFalse(view["system_prompt_custom"])
        finally:
            server.close()
            tmp.cleanup()

    def test_unknown_server_fields_ignored(self):
        server, tmp = _make_ui_server()
        try:
            status, body = server.json("/ui/config", body={"server": {"host": "0.0.0.0", "port": 1337, "system_prompt": "X"}})
            self.assertEqual(status, 200)
            with open(os.path.join(tmp.name, "config.json"), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data["server"]["host"], "127.0.0.1")
            self.assertEqual(data["server"].get("port"), 0)
            self.assertEqual(data["server"]["system_prompt"], "X")
        finally:
            server.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()