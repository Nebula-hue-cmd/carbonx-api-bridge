"""Provider adapter base classes.

A provider adapter turns the bridge's *normalized* request into the
upstream vendor call and normalizes the result back. Adapters are pure
configuration+HTTP objects: they hold no shared state, so the registry can
build one per configured name.

Normalized request (what /v1/chat and /v1/stream accept):
    {
        "messages": [{"role": "system|user|assistant|tool", "content": str}],
        "system": str|None,          # convenience for the system prompt
        "temperature": float|None,
        "max_tokens": int|None,      # already clamped server-side
        "model": str|None,           # falls back to provider default_model
        "json_mode": bool,           # request structured JSON output
        "include_usage": bool,       # request usage in the stream tail
        "files": [...],              # validated, not ingested (v1)
        "docs": [...],
    }

Normalized result (returned by every adapter's ``chat``):
    {"provider": name, "model": resolved_model, "content": str,
     "usage": {"prompt_tokens","completion_tokens","total_tokens"} | None,
     "finish_reason": str|None}
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..errors import ApiError, BadRequest, UpstreamError, StreamingUnsupported
from ..redact import redact
from ..streaming import parse_sse


def usage_norm(usage):
    """Normalize vendor usage dicts to a uniform shape (or None)."""
    if not isinstance(usage, dict):
        return None
    u = {}
    for a, b in (
        ("prompt_tokens", usage.get("prompt_tokens") or usage.get("input_tokens")),
        ("completion_tokens", usage.get("completion_tokens") or usage.get("output_tokens")),
    ):
        if b is not None:
            u[a] = int(b)
    if u:
        u["total_tokens"] = (u.get("prompt_tokens") or 0) + (u.get("completion_tokens") or 0)
    return u or None


class Provider:
    """Base for all adapters."""

    kind = "base"
    supports_streaming = False
    supports_json = False

    def __init__(self, name, cfg):
        self.name = name
        self.cfg = cfg or {}
        self.model = cfg.get("default_model")

    # -- configuration helpers ------------------------------------------
    def _resolve_model(self, params):
        model = params.get("model")
        if model in (None, "", "@default"):
            return self.model
        return model

    def _headers_extra(self):
        out = {}
        for key, value in (self.cfg.get("headers") or {}).items():
            out[key] = resolve_cfg(value)
        return out

    # -- transport -------------------------------------------------------
    def _request(self, url, headers, body=None, timeout=90):
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
        for key, value in headers.items():
            req.add_header(key, value)
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as ex:
            raw = ex.read()
            raise self._upstream_error(ex.code, raw)
        except urllib.error.URLError as ex:
            raise UpstreamError("upstream_connect_error: %s" % redact(str(ex)))

    def _read_json(self, res):
        return json.loads(res.read().decode("utf-8"))

    @staticmethod
    def _upstream_error(status, raw_body):
        raise ApiError(
            "upstream_error",
            "provider returned HTTP %d: %s"
            % (status, redact(raw_body.decode("utf-8", "replace"))[:500]),
            code=502,
        )

    # -- adapter API -----------------------------------------------------
    def chat(self, params):
        raise NotImplementedError

    def stream(self, params, emit):
        raise StreamingUnsupported(
            "provider '%s' does not support streaming; use /v1/chat"
            % (self.name,)
        )

    def known_models(self):
        return []

    def models(self):
        known = self.known_models()
        defaults = [self.model] if self.model else []
        extra = self.cfg.get("models") or []
        seen, items = set(), []
        for mid in list(known) + list(defaults) + list(extra):
            if mid and mid not in seen:
                seen.add(mid)
                entry = {"provider": self.name, "model": mid, "supports_streaming": self.supports_streaming}
                if self.supports_json:
                    entry["supports_json"] = True
                items.append(entry)
        if not items:
            items.append(
                {"provider": self.name, "model": "<default>", "supports_streaming": self.supports_streaming}
            )
        return items


def resolve_cfg(value):
    from ..config import resolve

    return resolve(value)


def clean_messages(params):
    """Build the ordered (role, content) list providers understand.

    A top-level ``system`` convenience is folded into the first position
    when the caller supplied it, without duplicating an existing system
    message.
    """
    messages = list((params.get("messages") or []))
    system = params.get("system")
    if system and not any(m.get("role") == "system" for m in messages):
        messages = [{"role": "system", "content": system}] + messages
    return messages