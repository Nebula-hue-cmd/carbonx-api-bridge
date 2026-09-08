"""Anthropic Messages API adapter.

Verified wire shape (2026): POST https://api.anthropic.com/v1/messages,
headers ``x-api-key`` + ``anthropic-version`` + ``content-type``, body
requires ``model``/``max_tokens``/``messages``. Streaming sends
``event:`` + ``data:`` SSE pairs; that dual format is handled by
:func:`carbonx_bridge.streaming.parse_sse`.

Upstream errors carry a ``{type:"error", error:{type, message}}`` body;
reported HTTP codes include 400 invalid_request_error, 401
authentication_error, 402 billing_error, 403 permission_error, 404
not_found_error, 429 rate_limit_error, 500 api_error, 529 overloaded.
"""

from __future__ import annotations

import json

from ..errors import ApiError, BadRequest, UpstreamError
from ..redact import redact
from ..streaming import parse_sse
from .base import Provider, clean_messages, usage_norm

ANTHROPIC_KNOWN = {
    "claude-3-5-sonnet-latest": 200000,
    "claude-3-7-sonnet-latest": 200000,
    "claude-sonnet-4-20250514": 200000,
    "claude-sonnet-4-5": 200000,
    "claude-opus-4-1-20250805": 200000,
    "claude-haiku-4-5": 200000,
}

_VERSION = "2023-06-01"


class Anthropic(Provider):
    kind = "anthropic"
    supports_streaming = True
    supports_json = False  # structured output uses tool use; not wired in v1

    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        self.base = (cfg.get("base_url") or "https://api.anthropic.com").rstrip("/")
        key = cfg.get("api_key") or ""
        self.key = resolve_cfg(key) if key else None

    def _headers(self, streaming=False):
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": self.cfg.get("anthropic_version") or _VERSION,
            "Accept": "text/event-stream" if streaming else "application/json",
        }
        if self.key:
            headers["x-api-key"] = self.key
        headers.update(self._headers_extra())
        return headers

    def _body(self, params):
        model = self._resolve_model(params)
        if not model:
            raise BadRequest("no model given and provider '%s' has no default_model" % (self.name,))
        raw = clean_messages(params)
        system = None
        messages = []
        for msg in raw:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                messages.append({"role": msg["role"], "content": msg["content"]})
        if not messages:
            raise BadRequest("anthropic requires at least one non-system message")
        body = {"model": model, "max_tokens": params.get("max_tokens") or 1024, "messages": messages}
        if system:
            body["system"] = system
        if params.get("temperature") is not None:
            body["temperature"] = params["temperature"]
        if params.get("json_mode"):
            body["temperature"] = 0
        return body

    def _endpoint(self):
        return self.base + "/v1/messages"

    def chat(self, params):
        body = self._body(params)
        res = self._request(self._endpoint(), self._headers(), body=body)
        data = self._read_json(res)
        self._raise_inline_error(data)
        blocks = data.get("content") or []
        content = "".join(block.get("text", "") for block in blocks if isinstance(block, dict) and block.get("type") in ("text", "text_delta"))
        return {
            "provider": self.name,
            "model": data.get("model") or body["model"],
            "content": content,
            "usage": usage_norm(data.get("usage")),
            "finish_reason": data.get("stop_reason"),
        }

    def stream(self, params, emit):
        body = self._body(params)
        body["stream"] = True
        res = self._request(self._endpoint(), self._headers(streaming=True), body=body)
        usage = None
        for event, payload in parse_sse(res):
            if payload is None or event == "message_stop":
                break
            if not payload:
                continue
            data = json.loads(payload)
            etype = data.get("type")
            if etype == "error":
                err = data.get("error") or {}
                raise ApiError("upstream_error", "upstream_stream_error: %s" % redact(str(err)), code=502)
            if etype == "content_block_delta":
                delta = data.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    emit(delta["text"])
            if etype == "message_delta" and data.get("usage"):
                usage = usage_norm(data["usage"]) or usage
        return {"model": body["model"], "usage": usage}

    def known_models(self):
        return sorted(ANTHROPIC_KNOWN)

    def models(self):
        items = super().models()
        for item in items:
            window = ANTHROPIC_KNOWN.get(item["model"])
            if window:
                item["context_window"] = window
        return items

    @staticmethod
    def _raise_inline_error(data):
        if not isinstance(data, dict):
            return
        err = data.get("error") if data.get("type") == "error" else None
        if err is not None:
            code = {"authentication_error": 401, "permission_error": 403, "rate_limit_error": 429}.get(err.get("type"), 502)
            raise UpstreamError("upstream_error: %s" % redact(str(err.get("message") or err)), code=code)


def resolve_cfg(value):
    from ..config import resolve

    return resolve(value)