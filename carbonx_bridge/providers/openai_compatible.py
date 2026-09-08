"""OpenAI-compatible chat completions adapter.

Also the base class for OpenRouter and OpenCode, which reuse the same wire
format. ``base_url`` may be pointed at any OpenAI-compatible server
(e.g. Ollama's ``http://localhost:11434/v1``); the adapter does not
assume any particular host.
"""

from __future__ import annotations

import json

from ..errors import ApiError, BadRequest, UpstreamError
from ..redact import redact
from ..streaming import parse_sse
from .base import Provider, clean_messages, usage_norm

OPENAI_KNOWN = {
    "gpt-4o-mini": 128000,
    "gpt-4o": 128000,
    "gpt-4.1-mini": 1047576,
    "gpt-4.1": 1047576,
    "gpt-4.5": 128000,
    "gpt-5": 400000,
    "gpt-5-mini": 400000,
    "o3-mini": 200000,
    "o1": 200000,
    "o1-mini": 128000,
}


class OpenAICompatible(Provider):
    kind = "openai"
    supports_streaming = True
    supports_json = True

    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        self.base = (cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        key = cfg.get("api_key") or ""
        self.key = resolve_cfg(key) if key else None

    # -- headers ---------------------------------------------------------
    def _headers(self, streaming=False):
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if streaming else "application/json",
        }
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        headers.update(self._headers_extra())
        return headers

    # -- request building ------------------------------------------------
    def _body(self, params):
        model = self._resolve_model(params)
        if not model:
            raise BadRequest("no model given and provider '%s' has no default_model" % (self.name,))
        body = {"model": model, "messages": clean_messages(params)}
        if params.get("temperature") is not None:
            body["temperature"] = params["temperature"]
        if params.get("max_tokens") is not None:
            body["max_tokens"] = params["max_tokens"]
        if params.get("json_mode"):
            body["response_format"] = {"type": "json_object"}
        return body

    def _endpoint(self):
        return self.base + "/chat/completions"

    # -- chat ------------------------------------------------------------
    def chat(self, params):
        body = self._body(params)
        res = self._request(self._endpoint(), self._headers(), body=body)
        data = self._read_json(res)
        self._raise_inline_error(data)
        choices = data.get("choices") or []
        message = choices[0].get("message") or {} if choices else {}
        content = message.get("content") or ""
        if isinstance(content, list):  # some compliance models return parts
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return {
            "provider": self.name,
            "model": data.get("model") or body["model"],
            "content": content,
            "usage": usage_norm(data.get("usage")),
            "finish_reason": (choices[0].get("finish_reason") if choices else None),
        }

    # -- stream ----------------------------------------------------------
    def stream(self, params, emit):
        body = self._body(params)
        body["stream"] = True
        body["stream_options"] = {"include_usage": bool(params.get("include_usage", True))}
        res = self._request(self._endpoint(), self._headers(streaming=True), body=body)
        usage = None
        for _event, payload in parse_sse(res):
            if payload is None:
                break
            data = json.loads(payload)
            if isinstance(data, dict) and data.get("error"):
                raise ApiError("upstream_error", "upstream_stream_error: %s" % redact(str(data["error"])), code=502)
            if isinstance(data, dict) and data.get("usage"):
                usage = usage_norm(data["usage"])
            choices = (data or {}).get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            fragment = delta.get("content")
            if fragment:
                emit(fragment)
            if choices[0].get("finish_reason"):
                break
        return {"model": body["model"], "usage": usage}

    # -- models ----------------------------------------------------------
    def known_models(self):
        return sorted(OPENAI_KNOWN)

    def models(self):
        items = super().models()
        for item in items:
            window = OPENAI_KNOWN.get(item["model"])
            if window:
                item["context_window"] = window
        return items

    # -- error handling --------------------------------------------------
    @staticmethod
    def _raise_inline_error(data):
        err = data.get("error") if isinstance(data, dict) else None
        if err is not None:
            status = err.get("code")
            if isinstance(status, int) and 400 <= status <= 599:
                code = status
            elif status and str(status) in ("invalid_api_key", "authentication_error"):
                code = 401
            else:
                code = 502
            raise UpstreamError(
                "upstream_error: %s" % redact(str(err.get("message") or err)),
                code=code,
            )


def resolve_cfg(value):
    from ..config import resolve

    return resolve(value)