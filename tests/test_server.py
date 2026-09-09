"""End-to-end HTTP server tests (real socket, real Handler)."""

from __future__ import annotations

import json
import unittest

from tests.helpers import BridgeTest, RunningServer, make_config


class TestHealth(unittest.TestCase):
    def test_health_is_public(self):
        srv = RunningServer(make_config())
        try:
            status, data = srv.json("/health")
            self.assertEqual(status, 200)
            self.assertTrue(data["ok"])
            self.assertEqual(data["name"], "carbonx-api-bridge")
            self.assertIn("mock", data["providers"])
        finally:
            srv.close()

    def test_unknown_route_404(self):
        srv = RunningServer(make_config())
        try:
            status, data = srv.json("/nope")
            self.assertEqual(status, 404)
            self.assertEqual(data["error"]["type"], "not_found")
        finally:
            srv.close()


class TestModels(unittest.TestCase):
    def test_models_require_auth(self):
        srv = RunningServer(make_config())
        try:
            status, data = srv.json("/v1/models")
            self.assertEqual(status, 401)
            self.assertEqual(data["error"]["type"], "auth_required")
        finally:
            srv.close()

    def test_models_ok_with_token(self):
        srv = RunningServer(make_config())
        try:
            status, data = srv.json("/v1/models", token="test-token-admin")
            self.assertEqual(status, 200)
            self.assertTrue(data["objects"])
            first = data["objects"][0]
            self.assertEqual(first["provider"], "mock")
            self.assertIn("supports_streaming", first)
        finally:
            srv.close()


class TestChat(BridgeTest):
    def test_unauthorized_401(self):
        status, data = self.server.json("/v1/chat", body={"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(status, 401)

    def test_chat_ok(self):
        status, data = self.server.json(
            "/v1/chat",
            body={"messages": [{"role": "user", "content": "hello"}]},
            token=self.token_admin,
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["content"], "hello from mock")
        self.assertEqual(data["provider"], "mock")
        self.assertEqual(data["model"], "mock/m1")
        self.assertTrue(data["id"].startswith("chat_"))
        self.assertIn("usage", data)

    def test_unknown_provider_400(self):
        status, data = self.server.json(
            "/v1/chat",
            body={"provider": "nope", "messages": [{"role": "user", "content": "hi"}]},
            token=self.token_admin,
        )
        self.assertEqual(status, 400)
        self.assertIn("unknown provider", data["error"]["message"])

    def test_oversized_body_413(self):
        status, data = self.server.json(
            "/v1/chat",
            body={"messages": [{"role": "user", "content": "x" * 5000}]},
            token=self.token_admin,
        )
        # either the 413 header check or the turn/prompt validation catches it;
        # the important guarantee is not a 200.
        self.assertIn(status, (400, 413))
        if status == 413:
            self.assertEqual(data["error"]["type"], "payload_too_large")

    def test_invalid_json_400(self):
        status, _, raw = self.server.request("/v1/chat", method="POST", body=b"{not json", token=self.token_admin)
        self.assertEqual(status, 400)


class TestStream(BridgeTest):
    def test_stream_requires_auth(self):
        status, data = self.server.json("/v1/stream", body={"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(status, 401)

    def test_stream_yields_chunks_then_done(self):
        status, headers, raw = self.server.request(
            "/v1/stream",
            method="POST",
            body={"messages": [{"role": "user", "content": "hi"}]},
            token=self.token_admin,
        )
        self.assertEqual(status, 200)
        self.assertTrue(headers.get("Content-Type", "").startswith("text/event-stream"))
        text = raw.decode("utf-8")
        self.assertIn('"type":"content"', text)
        self.assertIn("delta-one", text)
        self.assertIn("delta-two", text)
        self.assertIn("data: [DONE]", text)


class TestAnonTier(unittest.TestCase):
    def test_anon_chat_when_allowed(self):
        cfg = make_config({"server": {"allow_anon": True}})
        srv = RunningServer(cfg)
        try:
            status, data = srv.json("/v1/chat", body={"messages": [{"role": "user", "content": "hi"}]})
            self.assertEqual(status, 200)
            self.assertEqual(data["content"], "hello from mock")
        finally:
            srv.close()


class TestGameContext(BridgeTest):
    def _payload(self):
        return {
            "game": "Adopt Me!",
            "players": [{"name": "Alice", "user_id": "123", "team": "Red"}, {"name": "Bob", "display_name": "Bobby"}],
            "files": [{"path": "workspace/Main", "name": "Main", "content": "local Players = game:GetService('Players')"}],
        }

    def test_requires_auth(self):
        status, data = self.server.json("/v1/game-context", body=self._payload())
        self.assertEqual(status, 401)
        status, data = self.server.json("/v1/game-context", method="DELETE")
        self.assertEqual(status, 401)

    def test_set_get_clear_lifecycle(self):
        status, data = self.server.json("/v1/game-context", body=self._payload(), token=self.token_admin)
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["game"], "Adopt Me!")
        self.assertEqual(data["players"], 2)
        self.assertEqual(data["files"], 1)

        status, view = self.server.json("/ui/game-context")
        self.assertEqual(status, 200)
        self.assertTrue(view["attached"])
        self.assertEqual(view["game"], "Adopt Me!")
        self.assertEqual(view["files"], 1)
        self.assertEqual(view["players"], 2)

        status, _ = self.server.json("/v1/game-context", method="DELETE", token=self.token_admin)
        self.assertEqual(status, 200)
        status, view = self.server.json("/ui/game-context")
        self.assertEqual(status, 200)
        self.assertFalse(view["attached"])

    def test_panel_clear(self):
        self.server.json("/v1/game-context", body=self._payload(), token=self.token_admin)
        status, _ = self.server.json("/ui/game-context", method="DELETE")
        self.assertEqual(status, 200)
        _, view = self.server.json("/ui/game-context")
        self.assertFalse(view["attached"])

    def test_oversized_files_413(self):
        files = [{"path": "f%d" % i, "content": "x"} for i in range(60)]
        status, data = self.server.json("/v1/game-context", body={"game": "g", "files": files}, token=self.token_admin)
        self.assertEqual(status, 413)
        self.assertEqual(data["error"]["type"], "payload_too_large")

    def test_bad_body_400(self):
        status, data = self.server.json("/v1/game-context", body={"files": "nope"}, token=self.token_admin)
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()