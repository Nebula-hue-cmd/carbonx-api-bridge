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
import secrets
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config as cfg_mod
from . import ui
from .auth import Authenticator
from .errors import (
    ApiError,
    BadRequest,
    Forbidden,
    NotFound,
    UpstreamError,
)
from .limits import Validator, parse_json
from .providers import build_all, pick
from .redact import redact
from .streaming import done, error_chunk, stream_provider_events

__version__ = "1.3.0"

QUIET_ENDPOINTS = {"/v1/stream"}


class App:
    """Tiny composition root so tests can build an instance easily."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.authenticator = Authenticator(cfg["auth"], cfg["server"])
        self.validator = Validator(cfg["limits"])
        self.providers = build_all(cfg["providers"])
        self.default_provider = cfg["default_provider"]

    # ---- request handling ---------------------------------------------
    def handle_chat(self, body: bytes, auth_header, remote_ip):
        auth = self.authenticator.authenticate(auth_header, remote_ip)
        self.authenticator.check_rate(auth, remote_ip)
        self.validator.check_body_size(body)
        payload = parse_json(body)
        self.validator.validate_chat_payload(payload)
        provider = pick(self.providers, payload.get("provider"), self.default_provider)
        params = normalize_params(self.validator, payload)
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
        return provider, normalize_params(self.validator, payload)

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
        """
        if not ui.host_is_loopback(self.headers.get("Host")):
            raise Forbidden("control panel only answers to localhost hostnames")
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if fetch_site and fetch_site.lower() not in ("same-origin", "none"):
            raise Forbidden("cross-site request blocked")
        origin = self.headers.get("Origin")
        if origin and not ui.host_is_loopback(origin.split("://", 1)[-1].split("/", 1)[0]):
            raise Forbidden("cross-origin request blocked")
        if not ui.is_loopback(self.client_address[0]):
            raise Forbidden("the control panel is loopback-only (must run on this machine)")

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
            for name, fields in (patch.get("providers") or {}).items():
                if not isinstance(fields, dict):
                    continue
                current = data.setdefault("providers", {}).setdefault(name, {})
                for key, value in fields.items():
                    if value is None:
                        continue
                    value = str(value).strip()
                    if key == "api_key":
                        if value:  # empty string means "keep the current key"
                            current["api_key"] = value
                    elif value:
                        current[key] = value

        try:
            cfg = _ui_persist(self.app.config_path, mutator)
        except ValueError as ex:
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
    }


def _ui_persist(config_path, mutator):
    """Edit the on-disk config, validate it, write atomically, and reload.

    Returns the freshly loaded config; raises ValueError on invalid edits.
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
    os.replace(tmp, path)
    return cfg_mod.load_config(path)


def _swap_app(handler, cfg):
    """Hot-swap the running App (and global CONFIG) without a restart."""
    app = App(cfg)
    app.config_path = handler.app.config_path
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