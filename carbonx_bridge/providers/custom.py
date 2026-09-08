"""Bring-your-own 'custom' provider adapter.

Catches every AI service that is NOT one of the named vendors: Cursor's
local proxy, any local server (vLLM, LiteLLM, llama.cpp, LM Studio,
Open WebUI, ...), or a commercial endpoint that exposes either the OpenAI
or the Anthropic wire format.

Configure with ``"type": "custom"`` (alias ``"other"``) plus:

* ``base_url``  — REQUIRED. Root of the endpoint, e.g. Cursor's
  ``http://localhost:3000``. No default on purpose: a custom provider
  must point at something you own or supply.
* ``api_style`` — ``"openai"`` (default) or ``"anthropic"``. Selects which
  wire format to speak (Cursor exposes both).
* ``api_key``   — optional. Sent as ``Authorization: Bearer`` (openai) or
  ``x-api-key`` (anthropic) when present; omitted otherwise.
* ``headers``   — optional dict of extra headers, merged onto every call
  (``env:VAR`` values resolved at startup).
* ``default_model`` / ``models`` — as with any provider.

The adapter delegates to the same internals as the named providers, so curl
shapes, error mapping, and streaming behavior are identical.
"""

from __future__ import annotations

from ..errors import ConfigError
from .anthropic import Anthropic
from .base import Provider
from .openai_compatible import OpenAICompatible

_STYLES = ("openai", "anthropic")


class CustomProvider(Provider):
    kind = "custom"

    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        base_url = (cfg.get("base_url") or "").strip()
        if not base_url:
            raise ConfigError(
                "custom provider '%s' needs a 'base_url' (e.g. http://localhost:3000 for Cursor); "
                "no default exists because custom points at your own endpoint" % name
            )
        style = (cfg.get("api_style") or "openai").strip().lower()
        if style not in _STYLES:
            raise ConfigError(
                "custom provider '%s': api_style must be 'openai' or 'anthropic', got '%s'"
                % (name, cfg.get("api_style"))
            )
        impl_cfg = dict(cfg)
        impl_cfg["base_url"] = base_url
        if style == "anthropic":
            self._impl = Anthropic(name, impl_cfg)
        else:
            self._impl = OpenAICompatible(name, impl_cfg)
        self.supports_streaming = self._impl.supports_streaming
        self.supports_json = self._impl.supports_json

    # -- delegation ------------------------------------------------------
    def chat(self, params):
        return self._impl.chat(params)

    def stream(self, params, emit):
        return self._impl.stream(params, emit)

    def known_models(self):
        return self._impl.known_models()

    def models(self):
        return self._impl.models()