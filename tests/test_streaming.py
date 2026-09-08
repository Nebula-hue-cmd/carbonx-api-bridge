"""SSE framing tests."""

from __future__ import annotations

import json
import unittest

from carbonx_bridge.streaming import done, encode_chunk, parse_sse


def lines(*parts):
    return [p.encode("utf-8") for p in parts]


class TestParseSse(unittest.TestCase):
    def test_openai_style_data_lines(self):
        events = list(parse_sse(lines("data: {\"x\":1}", "", "data: {\"x\":2}", "data: [DONE]")))
        self.assertEqual(len(events), 3)
        self.assertEqual(json.loads(events[0][1])["x"], 1)
        self.assertIsNone(events[-1][0])
        self.assertIsNone(events[-1][1])

    def test_anthropic_event_data_pairs(self):
        raw = [
            "event: message_start",
            "data: {\"type\":\"message_start\",\"message\":{\"usage\":{\"input_tokens\":5}}}",
            "",
            "event: content_block_delta",
            "data: {\"type\":\"content_block_delta\",\"delta\":{\"text\":\"Hello\"}}",
            "",
            "event: message_stop",
            "data: {\"type\":\"message_stop\"}",
        ]
        events = list(parse_sse(lines(*raw)))
        names = [e[0] for e in events]
        self.assertEqual(names, ["message_start", "content_block_delta", "message_stop"])
        self.assertEqual(events[1][1], "{\"type\":\"content_block_delta\",\"delta\":{\"text\":\"Hello\"}}")

    def test_ignores_comments_and_keepalive(self):
        events = list(parse_sse(lines(": keep-alive", "", "data: {\"ok\":true}")))
        self.assertEqual(len(events), 1)

    def test_encoders(self):
        out = encode_chunk({"type": "content", "delta": "hi"})
        self.assertTrue(out.startswith(b"data: {"))
        self.assertTrue(out.endswith(b"\n\n"))
        self.assertEqual(done(), b"data: [DONE]\n\n")


if __name__ == "__main__":
    unittest.main()