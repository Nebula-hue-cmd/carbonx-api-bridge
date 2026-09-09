"""CarbonX API bridge â€” HTTP layer.

Endpoints (all JSON, ``application/json`` unless noted):

    GET  /health          -> {"ok": true, "version": .., "providers": [..]}
    GET  /v1/models       -> {"objects": [{provider, model, ...}]}
    POST /v1/chat         -> {"id", "provider", "model", "content", "usage", "finish_reason"}
    POST /v1/stream       -> text/event-stream (see carbonx_bridge.streaming)

Error responses always use the clean shape from
:mod:`carbonx_bridge.errors`; only the documented statuses are emitted.
Secrets are redacted at every logging boundary; Authorization headers and
request bodies are never logged.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config as cfg_mod
from . import ui
from .auth import Authenticator
from .errors import (
    ApiError,
    BadRequest,
    ConfigError,
    Forbidden,
    NotFound,
    UpstreamError,
)
from .limits import Validator, parse_json
from .providers import build_all, pick
from .redact import redact
from .streaming import done, error_chunk, stream_provider_events

__version__ = "1.4.0"

QUIET_ENDPOINTS = {"/v1/stream"}

# Origin schemes that only a locally installed browser add-on can produce
# (forbidden header names, so remote pages can't forge them). The add-on reads
# the panel's DOM anyway via content scripts, so tolerating it on the admin
# surface costs no security while keeping extensions like Merlin working.
_EXTENSION_SCHEMES = frozenset(
    {
        "chrome-extension",  # Chrome / Edge
        "edge-extension",
        "ms-browser-extension",  # Edge (legacy)
        "moz-extension",  # Firefox
        "safari-web-extension",  # Safari
    }
)

_PROVIDER_NAME_RE = re.compile(r"^[a-z0-9_\-]{1,32}$", re.IGNORECASE)

#: Provider settings the panel may write through /ui/config. Everything else
#: is ignored, so a hostile page that somehow reached this endpoint can't
#: smuggle arbitrary keys into config.json.
_ALLOWED_PROVIDER_FIELDS = frozenset(
    {
        "type",
        "api_style",
        "base_url",
        "api_key",
        "default_model",
        "models",
        "x_opencode_session",
        "headers",
    }
)

#: Server-wide settings the panel may write through /ui/config.
_ALLOWED_SERVER_FIELDS = frozenset({"system_prompt"})


class App:
    """Tiny composition root so tests can build an instance easily."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.authenticator = Authenticator(cfg["auth"], cfg["server"])
        self.validator = Validator(cfg["limits"])
        self.providers = build_all(cfg["providers"])
        self.default_provider = cfg["default_provider"]
        self.system_prompt = (cfg["server"].get("system_prompt") or "").strip() or None
        #: Snapshot of the user's game pushed by the Lumen executor via
        #: ``POST /v1/game-context`` (game, players, decompiled files).
        self.game_ctx = None

    # ---- request handling ---------------------------------------------
    def _inject_context(self, params):
        """Layer the bridge identity prompt and any game snapshot on top of
        what the client sent.

        Order of precedence: the client's own top-level ``system`` (or an
        existing system-role message) always wins as the lead; the configured
        identity prompt fills in when the client sent none; the game snapshot
        is appended so the model can answer questions about the game, its
        players and its files. Providers fold the result into a single
        leading system message via ``clean_messages``.
        """
        system = (params.get("system") or "").strip()
        if not system:
            system = self.system_prompt or ""
        ctx = self._build_game_context()
        if ctx:
            system = (system + "\n\n" + ctx) if system else ctx
        if system:
            params["system"] = system
        return params

    def _build_game_context(self):
        """Render the stored Lumen game snapshot as model-facing context."""
        c = self.game_ctx or {}
        out = []
        if c.get("game") or c.get("players") or c.get("files"):
            out.append("Real-time game context attached by the Lumen executor running for you:")
            if c.get("game"):
                out.append("Game: %s" % c["game"])
        players = c.get("players") or []
        if players:
            rows = []
            for p in players:
                name = p.get("name") or p.get("display_name") or "?"
                parts = [name]
                if p.get("display_name") and p["display_name"] != name:
                    parts.append("(display: %s)" % p["display_name"])
                if p.get("user_id"):
                    parts.append("userId=%s" % p["user_id"])
                if p.get("team"):
                    parts.append("team=%s" % p["team"])
                rows.append("- " + " ".join(parts))
            out.append("Players (%d):\n%s" % (len(players), "\n".join(rows)))
        files = c.get("files") or []
        if files:
            out.append(
                "Game files (%d) - the decompiled scripts/instances in the user's game. "
                "Read these before answering questions about the game, its mechanics, or "
                "before writing code for it:" % len(files)
            )
            for f in files:
                out.append("=== %s ===" % (f.get("path") or f.get("name") or "?"))
                out.append(f.get("content") or "")
        return "\n".join(out) if out else ""

    def _set_game_context(self, payload):
        """Validate and store a Lumen game snapshot (see README)."""
        if payload is None or not isinstance(payload, dict):
            raise BadRequest("body must be a JSON object")
        players = payload.get("players") or []
        files = payload.get("files") or []
        if not isinstance(players, list):
            raise BadRequest("'players' must be an array")
        if not isinstance(files, list):
            raise BadRequest("'files' must be an array")
        max_files = self.validator._max("max_game_files", 40)
        max_chars = self.validator._max("max_game_chars", 60000)
        if len(files) > max_files:
            from .errors import PayloadTooLarge

            raise PayloadTooLarge(
                "game context has %d files; limit is %d" % (len(files), max_files),
            )

        def clip(value, limit):
            value = str(value or "")
            return value[:limit]

        clean_players = []
        for p in players[:50]:
            if not isinstance(p, dict):
                continue
            clean_players.append(
                {
                    "name": clip(p.get("name"), 120),
                    "display_name": clip(p.get("display_name"), 120),
                    "user_id": str(clip(p.get("user_id"), 40)),
                    "team": clip(p.get("team"), 120),
                }
            )

        budget = max_chars
        truncated = 0
        clean_files = []
        for f in files:
            path = clip(f.get("path") or f.get("name") or "?", 400) if isinstance(f, dict) else "?"
            content = ""
            if isinstance(f, dict):
                content = str(f.get("content") or "")
            if budget <= 0:
                truncated += 1
                continue
            if len(content) > budget:
                content = content[:budget] + "\n[truncated]"
                truncated += 1
            budget -= len(content)
            clean_files.append({"path": path, "content": content})

        self.game_ctx = {
            "game": clip(payload.get("game"), 200),
            "players": clean_players,
            "files": clean_files,
            "chars": sum(len(f.get("content") or "") for f in clean_files),
            "at": time.time(),
        }
        return {
            "ok": True,
            "game": self.game_ctx["game"],
            "players": len(clean_players),
            "files": len(clean_files),
            "characters": self.game_ctx["chars"],
            "truncated_files": truncated,
        }

    def _clear_game_context(self):
        self.game_ctx = None
        return {"ok": True, "cleared": True}

    def handle_chat(self, body: bytes, auth_header, remote_ip):
        auth = self.authenticator.authenticate(auth_header, remote_ip)
        self.authenticator.check_rate(auth, remote_ip)
        self.validator.check_body_size(body)
        payload = parse_json(body)
        self.validator.validate_chat_payload(payload)
        provider = pick(self.providers, payload.get("provider"), self.default_provider)
        params = self._inject_context(normalize_params(self.validator, payload))
        try:
            result = provider.chat(params)
        except ApiError:
            raise
        except Exception as ex:  # noqa: BLE001 - normalize
            raise UpstreamError(
                "upstream_error: %s" % redact(str(ex)), code=502
            )
        return {
            "id": "chat_" + uuid.uuid4().hex[:12],
            "provider": result["provider"],
            "model": result["model"],
            "content": result["content"],
            "usage": result["usage"],
            "finish_reason": result.get("finish_reason"),
        }

    def prepare_stream(self, body: bytes, auth_header, remote_ip):
        """Authenticate + validate + resolve a stream provider BEFORE any
        SSE bytes are written, so auth/validation failures are clean JSON
        errors (401/413/400), never a half-open 200 stream."""
        auth = self.authenticator.authenticate(auth_header, remote_ip)
        self.authenticator.check_rate(auth, remote_ip)
        self.validator.check_body_size(body)
        payload = parse_json(body)
        self.validator.validate_chat_payload(payload)
        provider = pick(self.providers, payload.get("provider"), self.default_provider)
        if not getattr(provider, "supports_streaming", False):
            from .errors import StreamingUnsupported

            raise StreamingUnsupported(
                "provider '%s' does not support streaming; use /v1/chat" % (provider.name,)
            )
        return provider, self._inject_context(normalize_params(self.validator, payload))

    def run_stream(self, provider, params, writer):
        try:
            stream_provider_events(provider, params, writer)
        except StopStreaming:
            raise
        except ApiError as ex:
            writer(error_chunk(ex))
        except Exception as ex:  # noqa: BLE001
            writer(error_chunk(UpstreamError("upstream_error: %s" % redact(str(ex)), code=502)))
        writer(done())


def normalize_params(validator, payload):
    max_cap = validator._max("max_response_tokens", 2048)
    mt = payload.get("max_tokens")
    params = {
        "messages": payload["messages"],
        "system": payload.get("system"),
        "temperature": payload.get("temperature"),
        "model": payload.get("model"),
        "json_mode": bool(payload.get("json_mode")),
        "include_usage": bool(payload.get("include_usage", True)),
        "files": payload.get("files"),
        "docs": payload.get("docs"),
    }
    if isinstance(mt, (int, float)) and mt > 0:
        params["max_tokens"] = min(int(mt), max_cap)
    return params


class Handler(BaseHTTPRequestHandler):
    server_version = "CarbonXBridge/" + __version__
    protocol_version = "HTTP/1.1"

    app = None  # set by create_server
    limit_status = 65536

    # -- plumbing --------------------------------------------------------
    def _send_json(self, status, obj, extra_headers=None):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Bridge-Version", __version__)
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _route(self):
        from .errors import PayloadTooLarge

        length = self.headers.get("Content-Length") or "0"
        try:
            length = int(length)
        except ValueError:
            length = 0
        cap = self.app.validator.max_body_bytes
        if cap and length > cap:
            raise PayloadTooLarge(
                "request body of %d bytes exceeds limit of %d bytes" % (length, cap)
            )
        return self.rfile.read(length)

    # -- wire ------------------------------------------------------------
    def do_GET(self):
        try:
            path = self.path.split("?")[0]
            if path == "/":
                return self._ui_page()
            if path == "/health":
                return self._health()
            if path == "/ui/status":
                return self._ui_status()
            if path == "/v1/models":
                return self._models()
            if path == "/ui/token":
                return self._ui_tokens()
            if path == "/ui/config":
                return self._ui_config()
            if path == "/ui/game-context":
                return self._ui_game_context()
            raise NotFound()
        except ApiError as ex:
            self._error(ex)
        except Exception as ex:  # noqa: BLE001
            self._error(_internal(ex))

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            if path == "/v1/chat":
                return self._chat()
            if path == "/v1/stream":
                return self._stream()
            if path == "/v1/game-context":
                return self._v1_game_context()
            if path == "/ui/token":
                return self._ui_new_token()
            if path == "/ui/config":
                return self._ui_save_config()
            raise NotFound()
        except StopStreaming:
            return
        except ApiError as ex:
            self._error(ex)
        except Exception as ex:  # noqa: BLE001
            self._error(_internal(ex))

    def do_DELETE(self):
        path = self.path.split("?")[0]
        try:
            if path == "/v1/game-context":
                return self._v1_game_context_clear()
            if path == "/ui/game-context":
                return self._ui_game_context_clear()
            raise NotFound()
        except ApiError as ex:
            self._error(ex)
        except Exception as ex:  # noqa: BLE001
            self._error(_internal(ex))

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.end_headers()

    # -- control panel (loopback-only) -----------------------------------
    def _guard_admin_surface(self):
        """Reject DNS-rebinding and CSRF aimed at / and /ui/*.

        * Host must name a loopback interface (rebinding lets an attacker
          site's domain resolve to 127.0.0.1 in *your* browser, which then
          reads the panel same-origin).
        * If the browser tells us how it navigated (Sec-Fetch-Site / Origin),
          it must be us (or a top-level navigation), not a cross-site page.
        * Locally installed browser add-ons (Merlin and friends) issue their
          own cross-site requests with an ``Origin`` of ``chrome-extension://``
          & co. Those origins can only be produced by an installed extension
          process -- a remote page cannot set the ``Origin`` header -- and they
          already read the panel's DOM through content scripts anyway, so they
          are tolerated. Everything else cross-site stays blocked.
        """
        if not ui.host_is_loopback(self.headers.get("Host")):
            raise Forbidden("control panel only answers to localhost hostnames")
        if not ui.is_loopback(self.client_address[0]):
            raise Forbidden("the control panel is loopback-only (must run on this machine)")
        origin = self.headers.get("Origin")
        if origin:
            scheme, _, rest = origin.lower().partition("://")
            host = rest.split("/", 1)[0]
            if scheme in _EXTENSION_SCHEMES and host:
                return  # local browser add-on, not an attacker site
            if not ui.host_is_loopback(host):
                raise Forbidden("cross-origin request blocked")
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if fetch_site and fetch_site.lower() not in ("same-origin", "none"):
            raise Forbidden("cross-site request blocked")

    def _ui_page(self):
        self._guard_admin_surface()
        data = ui.PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _ui_status(self):
        self._guard_admin_surface()
        return self._health()

    def _ui_tokens(self):
        self._guard_admin_surface()
        tokens = [
            {"token": token, "user": info.get("user", "?"), "tier": info.get("tier", "free")}
            for token, info in (self.app.cfg["auth"].get("tokens") or {}).items()
        ]
        self._send_json(200, {"tokens": tokens})

    def _ui_config(self):
        self._guard_admin_surface()
        self._send_json(200, _ui_config_view(self.app.cfg))

    def _ui_new_token(self):
        self._guard_admin_surface()
        patch = parse_json(self._route())
        if patch.get("action") == "delete":
            return self._ui_delete_token(patch)
        token = secrets.token_urlsafe(24)
        user = str(patch.get("user") or "you")
        tier = str(patch.get("tier") or "admin")

        def mutator(data):
            data.setdefault("auth", {}).setdefault("tokens", {})[token] = {"user": user, "tier": tier}

        try:
            cfg = _ui_persist(self.app.config_path, mutator)
        except ValueError as ex:
            raise BadRequest(str(ex))
        _swap_app(self, cfg)
        self._send_json(200, {"token": token, "user": user, "tier": tier})

    def _ui_delete_token(self, patch):
        target = str(patch.get("token") or "")
        if not target:
            raise BadRequest("no token given to delete")

        def mutator(data):
            tokens = data.setdefault("auth", {}).setdefault("tokens", {})
            if target not in tokens:
                raise ValueError("no such token")
            if len(tokens) <= 1:
                raise ValueError("cannot delete the last token (you would lock yourself out)")
            del tokens[target]

        try:
            cfg = _ui_persist(self.app.config_path, mutator)
        except ValueError as ex:
            raise BadRequest(str(ex))
        _swap_app(self, cfg)
        self._send_json(200, {"ok": True})

    def _ui_save_config(self):
        self._guard_admin_surface()
        patch = parse_json(self._route())

        def mutator(data):
            np = patch.get("default_provider")
            if np and isinstance(np, str) and np.strip():
                data["default_provider"] = np.strip()
            server_patch = patch.get("server")
            if server_patch is not None:
                if not isinstance(server_patch, dict):
                    raise ValueError("server must be an object")
                for key, value in server_patch.items():
                    if key not in _ALLOWED_SERVER_FIELDS:
                        continue
                    data.setdefault("server", {})[key] = str(value or "").strip()
            providers_patch = patch.get("providers") or {}
            if not isinstance(providers_patch, dict):
                raise ValueError("providers must be an object")
            existing = data.setdefault("providers", {})
            for name, fields in providers_patch.items():
                if not isinstance(name, str) or not _PROVIDER_NAME_RE.fullmatch(name):
                    raise ValueError("provider name must be [a-z0-9_-] (max 32)")
                if not isinstance(fields, dict):
                    continue
                current = dict(existing.get(name) or {})
                is_new = name not in existing
                for key, value in fields.items():
                    if key not in _ALLOWED_PROVIDER_FIELDS or value is None:
                        continue
                    value = str(value).strip()
                    if key == "api_key":
                        if value:  # empty string means "keep the current key"
                            current["api_key"] = value
                        continue
                    if value or key == "type":
                        current[key] = value
                if is_new:
                    ptype = current.get("type") or "openai"
                    if ptype not in cfg_mod.VALID_PROVIDER_TYPES:
                        raise ValueError("unknown provider type '%s'" % ptype)
                    if ptype in ("custom", "other", "opencode") and not current.get("base_url"):
                        raise ValueError("provider '%s' needs a base_url" % name)
                    if ptype == "opencode" and not current.get("x_opencode_session"):
                        raise ValueError("provider '%s' (opencode) needs x_opencode_session" % name)
                existing[name] = current

        try:
            cfg = _ui_persist(self.app.config_path, mutator)
        except (ValueError, ConfigError) as ex:
            raise BadRequest(str(ex))
        _swap_app(self, cfg)
        self._send_json(200, {"ok": True, "note": "applied without restart"})

    # -- endpoints -------------------------------------------------------
    def _health(self):
        self._send_json(
            200,
            {
                "ok": True,
                "name": "carbonx-api-bridge",
                "version": __version__,
                "providers": sorted(self.app.providers),
                "default_provider": self.app.default_provider,
                "require_auth": bool(self.app.cfg["server"].get("require_auth", True)),
            },
        )

    def _models(self):
        auth = self.app.authenticator.authenticate(
            self.headers.get("Authorization"), self.client_address[0]
        )
        self.app.authenticator.check_rate(auth, self.client_address[0])
        items = []
        for provider in self.app.providers.values():
            items.extend(provider.models())
        self._send_json(200, {"objects": items})

    def _chat(self):
        body = self._route()
        result = self.app.handle_chat(body, self.headers.get("Authorization"), self.client_address[0])
        self._send_json(200, result)

    def _v1_game_context(self):
        body = self._route()
        auth = self.app.authenticator.authenticate(self.headers.get("Authorization"), self.client_address[0])
        self.app.authenticator.check_rate(auth, self.client_address[0])
        self.app.validator.check_body_size(body)
        payload = parse_json(body)
        result = self.app._set_game_context(payload)
        self._send_json(200, result)

    def _v1_game_context_clear(self):
        auth = self.app.authenticator.authenticate(self.headers.get("Authorization"), self.client_address[0])
        self.app.authenticator.check_rate(auth, self.client_address[0])
        self._send_json(200, self.app._clear_game_context())

    def _ui_game_context(self):
        self._guard_admin_surface()
        c = self.app.game_ctx
        if not c:
            self._send_json(200, {"attached": False})
            return
        self._send_json(
            200,
            {
                "attached": True,
                "game": c.get("game"),
                "players": len(c.get("players") or []),
                "files": len(c.get("files") or []),
                "characters": c.get("chars", 0),
            },
        )

    def _ui_game_context_clear(self):
        self._guard_admin_surface()
        self._send_json(200, self.app._clear_game_context())

    def _stream(self):
        body = self._route()
        provider, params = self.app.prepare_stream(
            body, self.headers.get("Authorization"), self.client_address[0]
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Bridge-Version", __version__)
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.flush()
        self.app.run_stream(provider, params, self._emit)

    def _emit(self, data: bytes):
        try:
            self.wfile.write(data)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            raise StopStreaming()

    # -- error path ------------------------------------------------------
    def _error(self, err: ApiError):
        extra = {}
        if err.code == 429 and getattr(err, "retry_after", None):
            extra["Retry-After"] = str(int(getattr(err, "retry_after", 1)))
        self._send_json(err.code, err.as_dict(), extra)


class StopStreaming(Exception):
    pass


def _internal(ex):
    from .errors import ApiError

    return ApiError(
        "internal_error",
        "internal error: %s" % redact(str(type(ex).__name__)),
        500,
    )


def create_server(cfg, host=None, port=None, config_path=None):
    app = App(cfg)
    app.config_path = config_path or cfg_mod.DEFAULT_CONFIG_PATH
    server = ThreadingHTTPServer((host or cfg["server"]["host"], int(port or cfg["server"]["port"])), Handler)
    server.daemon_threads = True
    Handler.app = app
    return server


def _ui_config_view(cfg):
    providers = {}
    for name, pc in (cfg["providers"] or {}).items():
        entry = {"type": pc.get("type", "openai")}
        for key in ("api_style", "base_url", "default_model"):
            if pc.get(key):
                entry[key] = pc[key]
        if pc.get("models"):
            entry["models"] = list(pc["models"])
        entry["has_key"] = bool(pc.get("api_key"))  # value is NEVER returned
        providers[name] = entry
    return {
        "default_provider": cfg["default_provider"],
        "providers": providers,
        "server": {
            "host": cfg["server"].get("host"),
            "port": cfg["server"].get("port"),
            "require_auth": bool(cfg["server"].get("require_auth", True)),
        },
        "system_prompt": cfg["server"].get("system_prompt") or "",
        "system_prompt_default": cfg_mod.DEFAULT_SYSTEM_PROMPT,
        "system_prompt_custom": bool(
            cfg["server"].get("system_prompt")
            and cfg["server"].get("system_prompt") != cfg_mod.DEFAULT_SYSTEM_PROMPT
        ),
    }


def _ui_persist(config_path, mutator):
    """Edit the on-disk config, validate it, write atomically, and reload.

    The candidate is parsed, then fully validated (``load_config`` plus a
    dry-run of every provider build) while still in the ``.tmp`` file, so a
    bad save never lands in ``config.json`` and never breaks the next boot.
    Returns the freshly loaded config; raises ValueError/ConfigError on
    invalid edits.
    """
    path = config_path or cfg_mod.DEFAULT_CONFIG_PATH
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    else:
        data = {}
    mutator(data)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    try:
        cfg = cfg_mod.load_config(tmp)
        build_all(cfg["providers"])  # reject configs the bridge can't boot with
    except (ValueError, ConfigError):
        os.remove(tmp)
        raise
    os.replace(tmp, path)
    return cfg


def _swap_app(handler, cfg):
    """Hot-swap the running App (and global CONFIG) without a restart."""
    app = App(cfg)
    app.config_path = handler.app.config_path
    app.game_ctx = handler.app.game_ctx  # the live game snapshot is runtime state, not config
    Handler.app = app
    cfg_mod.CONFIG = cfg


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        prog="carbonx-bridge",
        description="CarbonX API Bridge â€” BYOK LLM gateway (REST + SSE).",
    )
    parser.add_argument("--version", action="version", version="carbonx-api-bridge %s" % __version__)
    parser.add_argument("--host", help="override server.host from config.json (default 127.0.0.1)")
    parser.add_argument("--port", type=int, help="override server.port from config.json (default 8787)")
    parser.add_argument(
        "--token",
        help="extra admin token (added to auth.tokens as user 'you', tier 'admin'); "
        "use when you do not want to edit config.json just to add a token",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="open the control panel in your browser once the bridge is up",
    )
    args = parser.parse_args(argv)

    created, generated_token = cfg_mod.ensure_config()
    if created:
        print("=" * 62)
        print("[bridge] No config.json found â€” I created one for you.")
        print("[bridge]")
        print("[bridge] Your access token (paste this into your Lumen/bridge settings):")
        print("[bridge]")
        print("[bridge]   %s" % generated_token)
        print("[bridge]")
        print("[bridge] The bridge is running on the built-in mock AI right now.")
        print("[bridge] To use a real model: open the control panel in your browser")
        print("[bridge]   ->  http://127.0.0.1:%s/   (paste an API key, click save)" % cfg_mod.DEFAULT_CONFIG.get("server", {}).get("port", 8787))
        print("[bridge] or edit config.json and paste a key into one of the providers.")
        print("=" * 62)

    try:
        cfg_mod.init()
    except ValueError as ex:
        print("[bridge] ERROR in config.json: %s" % ex)
        print("[bridge] Fix the file, or delete it and restart so one is created for you.")
        raise SystemExit(1)
    cfg = cfg_mod.CONFIG

    if args.token:
        cfg.setdefault("auth", {}).setdefault("tokens", {})
        cfg["auth"]["tokens"][args.token] = {"user": "you", "tier": "admin"}

    server = create_server(cfg, host=args.host, port=args.port, config_path=cfg_mod.DEFAULT_CONFIG_PATH)
    host, port = server.server_address[:2]
    print("[bridge] carbonx-api-bridge v%s" % __version__)
    print("[bridge] listening on http://%s:%s  (127.0.0.1 = THIS machine, always)" % (host, port))
    print("[bridge] control panel: http://127.0.0.1:%s/  (open in a browser on this PC)" % port)
    if args.open and host in ("127.0.0.1", "::1", "0.0.0.0"):
        import webbrowser

        webbrowser.open("http://127.0.0.1:%s/" % port)
    print("[bridge] providers: %s (default %s)" % (", ".join(sorted(cfg["providers"])) or "(none)", cfg["default_provider"]))
    require = cfg["server"].get("require_auth", True)
    anon = cfg["server"].get("allow_anon", False)
    if require and not anon:
        print("[bridge] auth required (Bearer token per auth.tokens)")
    elif require and anon:
        print("[bridge] auth required, but anon tier is enabled")
    else:
        print("[bridge] WARNING: auth is DISABLED (require_auth: false)")
    sys.stdout.flush()  # make startup logs/banners visible even when piped
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[bridge] shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()