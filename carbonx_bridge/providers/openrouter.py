"""OpenRouter adapter (OpenAI-compatible wire format).

Base: https://openrouter.ai/api/v1
* Uses ``Authorization: Bearer`` with your OpenRouter key.
* Optional ``HTTP-Referer`` / ``X-Title`` are supported via config
  ``headers`` (recommended by OpenRouter, never required here).
* OpenRouter returns an in-body ``error`` object even when the HTTP status
  is 200 (when the request reached a model), and streams an error chunk
  with ``finish_reason: "error"`` — both are surfaced as clean errors.

This adapter is intentionally thin: it inherits all OpenAI-compatible
transport/parsing logic and only fixes defaults + error normalization.
"""

from __future__ import annotations

from ..errors import UpstreamError
from ..redact import redact
from .openai_compatible import OpenAICompatible

OPENROUTER_KNOWN = {
    "meta-llama/llama-4-maverick": 1000000,
    "openai/gpt-4o-mini": 128000,
    "anthropic/claude-3.5-sonnet": 200000,
    "google/gemini-2.0-flash": 1000000,
}


class OpenRouter(OpenAICompatible):
    kind = "openrouter"
    supports_json = False  # model-dependent; do not advertise universally

    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        self.base = (cfg.get("base_url") or "https://openrouter.ai/api/v1").rstrip("/")

    def _headers_extra(self):
        headers = super()._headers_extra()
        for header in ("HTTP-Referer", "X-Title"):
            if header in headers:
                continue
            value = self.cfg.get(header.lower().replace("-", "_"))  # e.g. http_referer
            if value:
                headers[header] = resolve_cfg(value)
        return headers

    def known_models(self):
        return sorted(OPENROUTER_KNOWN)

    def models(self):
        items = super().models()
        for item in items:
            window = OPENROUTER_KNOWN.get(item["model"])
            if window:
                item["context_window"] = window
        return items

    @staticmethod
    def _raise_inline_error(data):
        err = data.get("error") if isinstance(data, dict) else None
        if err is not None:
            raise UpstreamError(
                "upstream_error: %s" % redact(str(err.get("message") or err)),
                code=502,
            )


def resolve_cfg(value):
    from ..config import resolve

    return resolve(value)