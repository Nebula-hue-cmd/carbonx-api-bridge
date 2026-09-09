"""Provider adapter tests.

Network adapters are tested against in-process fake HTTP upstreams so the
suite runs offline and deterministic.
"""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from carbonx_bridge.errors import ConfigError, UpstreamError
from carbonx_bridge.providers import build_provider


class FakeUpstream:
    """Serve canned responses the way a real vendor would."""

    def __init__(self, handler):
        self.handler = handler
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class CaptureHandler(BaseHTTPRequestHandler):
    requests = []
    response = (200, {"choices": [{"message": {"role": "assistant", "content": "ok"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 2}, "model": "m"})

    def log_message(self, *a):
        pass

    def do_POST(self):
        type(self).requests.append(self._read())
        status, body = type(self).response
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read(self):
        length = int(self.headers.get("Content-Length") or 0)
        return {
            "path": self.path,
            "headers": dict(self.headers),
            "body": json.loads(self.rfile.read(length).decode("utf-8")),
        }


class ConfigErrorBody(CaptureHandler):
    response = (200, {"choices": [{"message": {"content": "bad upstream says no"}}], "error": {"code": 400, "message": "upstream refused"}})


class FakeSSEHandler(CaptureHandler):
    def do_POST(self):
        type(self).requests.append(self._read())
        chunks = [
            b"data: {\"choices\": [{\"delta\": {\"content\": \"a\"}}]}\n\n",
            b"data: {\"choices\": [{\"delta\": {\"content\": \"b\"}}]}\n\n",
            b"data: {\"choices\": [{\"delta\": {}}], \"usage\": {\"prompt_tokens\": 3, \"completion_tokens\": 2}}\n\n",
            b"data: [DONE]\n\n",
        ]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(sum(len(c) for c in chunks)))
        self.end_headers()
        for c in chunks:
            self.wfile.write(c)


class TestMock(unittest.TestCase):
    def test_chat(self):
        p = build_provider("mock", {"type": "mock", "default_model": "mock/m1", "reply": "hi"})
        out = p.chat({"messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(out["content"], "hi")
        self.assertEqual(out["model"], "mock/m1")
        self.assertEqual(out["usage"]["total_tokens"], 7)

    def test_stream(self):
        p = build_provider("mock", {"type": "mock", "stream_pieces": ["a", "b"]})
        got = []
        final = p.stream({"messages": [{"role": "user", "content": "x"}]}, got.append)
        self.assertEqual(got, ["a", "b"])
        self.assertEqual(final["usage"]["total_tokens"], 7)


class TestOpenAICompatible(unittest.TestCase):
    def setUp(self):
        CaptureHandler.requests = []
        self.up = FakeUpstream(CaptureHandler)

    def tearDown(self):
        self.up.close()

    def test_chat_parses(self):
        p = build_provider("oai", {"type": "openai", "api_key": "sk-abcdefghijklmnopqrstuvwxyz", "base_url": self.up.url, "default_model": "m"})
        out = p.chat({"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(out["content"], "ok")
        self.assertEqual(out["model"], "m")
        self.assertEqual(out["usage"]["total_tokens"], 3)
        req = CaptureHandler.requests[-1]
        hs = {k.lower(): v for k, v in req["headers"].items()}
        self.assertNotIn("stream", req["body"])
        self.assertTrue(hs.get("authorization", "").startswith("Bearer "))

    def test_model_resolution_uses_provider_default(self):
        p = build_provider("oai", {"type": "openai", "api_key": "sk-abcdefghijklmnopqrstuvwxyz", "base_url": self.up.url, "default_model": "m"})
        for sent in ("@default", ""):
            out = p.chat({"messages": [{"role": "user", "content": "hi"}], "model": sent})
            self.assertEqual(out["model"], "m", repr(sent))
            self.assertEqual(CaptureHandler.requests[-1]["body"]["model"], "m")
        out = p.chat({"messages": [{"role": "user", "content": "hi"}]})  # no model at all
        self.assertEqual(out["model"], "m")
        self.assertEqual(CaptureHandler.requests[-1]["body"]["model"], "m")
        # explicit model still wins
        out = p.chat({"messages": [{"role": "user", "content": "hi"}], "model": "gpt-4o"})
        self.assertEqual(CaptureHandler.requests[-1]["body"]["model"], "gpt-4o")

    def test_stream_yields_deltas(self):
        up = FakeUpstream(FakeSSEHandler)
        try:
            p = build_provider("oai", {"type": "openai", "api_key": "k", "base_url": up.url, "default_model": "m"})
            got = []
            final = p.stream({"messages": [{"role": "user", "content": "hi"}]}, got.append)
            self.assertEqual(got, ["a", "b"])
            self.assertEqual(final["usage"]["prompt_tokens"], 3)
        finally:
            up.close()


class TestAnthropic(unittest.TestCase):
    class AnthropicHandler(CaptureHandler):
        response = (
            200,
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-x",
                "content": [{"type": "text", "text": "anthropic says hi"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 3, "output_tokens": 5},
            },
        )

    def test_chat_parses_and_uses_x_api_key(self):
        up = FakeUpstream(self.AnthropicHandler)
        try:
            p = build_provider("cl", {"type": "anthropic", "api_key": "sk-ant-api03-abcdefghijklmnopqrstuv", "base_url": up.url, "default_model": "claude-x"})
            out = p.chat({"messages": [{"role": "user", "content": "hi"}]})
            self.assertEqual(out["content"], "anthropic says hi")
            self.assertEqual(out["finish_reason"], "end_turn")
            req = self.AnthropicHandler.requests[-1]
            hs = {k.lower(): v for k, v in req["headers"].items()}
            self.assertEqual(hs.get("x-api-key"), "sk-ant-api03-abcdefghijklmnopqrstuv")
            self.assertEqual(hs.get("anthropic-version"), "2023-06-01")
            self.assertIn("max_tokens", req["body"])
        finally:
            up.close()


class TestOpenRouter(unittest.TestCase):
    def test_inbody_error_raised(self):
        up = FakeUpstream(ConfigErrorBody)
        try:
            p = build_provider("or", {"type": "openrouter", "api_key": "k", "base_url": up.url, "default_model": "m"})
            with self.assertRaises(UpstreamError):
                p.chat({"messages": [{"role": "user", "content": "hi"}]})
        finally:
            up.close()


class TestOpenCodeRequiresSession(unittest.TestCase):
    def test_refuses_without_session(self):
        with self.assertRaises(ConfigError):
            build_provider("oc", {"type": "opencode", "api_key": "k", "base_url": "http://127.0.0.1:1"})

    def test_builds_with_session(self):
        p = build_provider("oc", {"type": "opencode", "api_key": "k", "x_opencode_session": "abc123", "base_url": "http://127.0.0.1:1", "default_model": "m"})
        self.assertEqual(p.session, "abc123")


class FakeAnthropicHandler(CaptureHandler):
    response = (
        200,
        {
            "id": "msg_9",
            "type": "message",
            "role": "assistant",
            "model": "cursor-fast",
            "content": [{"type": "text", "text": "cursor says hi"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 2, "output_tokens": 3},
        },
    )


class TestCustomProvider(unittest.TestCase):
    """'custom'/'other' = bring-your-own endpoint (Cursor, local LLMs, ...)."""

    def setUp(self):
        CaptureHandler.requests = []
        FakeAnthropicHandler.requests = []
        self.up = FakeUpstream(CaptureHandler)
        self.au = FakeUpstream(FakeAnthropicHandler)

    def tearDown(self):
        self.up.close()
        self.au.close()

    def test_openai_style_calls_chat_completions_on_base_url(self):
        p = build_provider(
            "local",
            {"type": "custom", "api_style": "openai", "api_key": "k123", "base_url": self.up.url, "default_model": "m"},
        )
        out = p.chat({"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(out["content"], "ok")
        req = CaptureHandler.requests[-1]
        self.assertTrue(req["path"].startswith("/chat/completions"))
        hs = {k.lower(): v for k, v in req["headers"].items()}
        self.assertTrue(hs.get("authorization", "").startswith("Bearer k123"))

    def test_anthropic_style_speaks_messages_api(self):
        p = build_provider(
            "cursor",
            {"type": "custom", "api_style": "anthropic", "api_key": "sk-ant-api03-abcdefghijklmnopqrstuv", "base_url": self.au.url, "default_model": "cursor-fast"},
        )
        out = p.chat({"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(out["content"], "cursor says hi")
        self.assertEqual(out["finish_reason"], "end_turn")
        req = FakeAnthropicHandler.requests[-1]
        self.assertTrue(req["path"].startswith("/v1/messages"))
        hs = {k.lower(): v for k, v in req["headers"].items()}
        self.assertEqual(hs.get("x-api-key"), "sk-ant-api03-abcdefghijklmnopqrstuv")
        self.assertIn("max_tokens", req["body"])

    def test_extra_headers_merged(self):
        p = build_provider(
            "local",
            {"type": "other", "api_style": "openai", "base_url": self.up.url, "default_model": "m", "headers": {"X-Custom": "cookie", "Authorization": "Bearer deeply-custom"}},
        )
        p.chat({"messages": [{"role": "user", "content": "hi"}]})
        req = CaptureHandler.requests[-1]
        hs = {k.lower(): v for k, v in req["headers"].items()}
        self.assertEqual(hs.get("x-custom"), "cookie")
        self.assertEqual(hs.get("authorization"), "Bearer deeply-custom")

    def test_requires_base_url(self):
        with self.assertRaises(ConfigError):
            build_provider("bad", {"type": "custom", "api_key": "k"})

    def test_bad_api_style_rejected(self):
        with self.assertRaises(ConfigError):
            build_provider("bad", {"type": "custom", "api_style": "soap", "base_url": self.up.url})

    def test_other_alias_builds_as_custom(self):
        p = build_provider("x", {"type": "other", "api_style": "openai", "base_url": self.up.url, "default_model": "m"})
        self.assertEqual(p.kind, "custom")


if __name__ == "__main__":
    unittest.main()