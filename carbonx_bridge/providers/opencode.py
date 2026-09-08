"""OpenCode Zen "Go" adapter (opt-in, explicit session).

Background (verified 2026-09-07): OpenCode's free Zen tier at
``https://opencode.ai/zen/go/v1`` rejects requests that lack
``x-opencode-session`` with ``{"type":"error","error":{"type":"MissingSessionID"...}}``.
The free tier is designed to be used from inside an OpenCode session, so the
*.correct* way to bridge it is to give the bridge a **stable session id you
already own** (e.g. from your own OpenCode conversation) via
``x_opencode_session`` in the provider config.

This adapter therefore refuses to build unless ``x_opencode_session`` is
configured. It does NOT mint sessions, does not default the header, and does
not bypass anything. If you do not have a session id, use a real provider
key instead.

An optional ``api_key`` is passed through as ``Authorization: Bearer`` for
hosted/key-based OpenCode deployments.
"""

from __future__ import annotations

from ..errors import ConfigError
from .openai_compatible import OpenAICompatible

BASE = "https://opencode.ai/zen/go/v1"


class OpenCode(OpenAICompatible):
    kind = "opencode"
    supports_json = False

    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        self.base = (cfg.get("base_url") or BASE).rstrip("/")
        session = cfg.get("x_opencode_session")
        if not session:
            raise ConfigError(
                "provider '%s' (type=opencode) requires 'x_opencode_session' "
                "-- set it to a stable session id from your own OpenCode "
                "conversation. This bridge never mints sessions." % (name,)
            )
        self.session = session

    def _headers_extra(self):
        headers = super()._headers_extra()
        headers["x-opencode-session"] = self.session
        return headers

    def chat(self, params):
        return super().chat(params)

    def stream(self, params, emit):
        return super().stream(params, emit)

    def known_models(self):
        return [self.model] if self.model else ["opencode-default"]