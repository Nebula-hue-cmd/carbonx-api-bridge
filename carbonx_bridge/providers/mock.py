"""Deterministic fake provider for local testing.

Never a real network provider. Configure:

    "mock": {"type": "mock", "default_model": "mock/m1",
             "reply": "...", "stream_pieces": ["a ", "b ", "c"]}
"""

from __future__ import annotations

import time

from .base import Provider


class Mock(Provider):
    kind = "mock"
    supports_streaming = True

    def chat(self, params):
        return {
            "provider": self.name,
            "model": self._resolve_model(params) or "mock",
            "content": self.cfg.get("reply") or "mock reply",
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            "finish_reason": "stop",
        }

    def stream(self, params, emit):
        pieces = self.cfg.get("stream_pieces") or ["mock ", "reply ", "streamed ", "in deltas"]
        for piece in pieces:
            time.sleep(0.005)
            emit(piece)
        return {
            "model": self._resolve_model(params) or "mock",
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        }

    def known_models(self):
        return ["mock/m1"]