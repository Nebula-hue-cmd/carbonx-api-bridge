"""CarbonX API bridge — HTTP layer.

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
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config as cfg_mod
from .auth import Authenticator
from .errors import (
    ApiError,
    NotFound,
    UpstreamError,
)
from .limits import Validator, parse_json
from .providers import build_all, pick
from .redact import redact
from .streaming import done, error_chunk, stream_provider_events

__version__ = "1.1.0"

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
            if self.path.split("?")[0] == "/health":
                self._health()
            elif self.path.split("?")[0] == "/v1/models":
                self._models()
            else:
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


def create_server(cfg, host=None, port=None):
    app = App(cfg)
    server = ThreadingHTTPServer((host or cfg["server"]["host"], int(port or cfg["server"]["port"])), Handler)
    server.daemon_threads = True
    Handler.app = app
    return server


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        prog="carbonx-bridge",
        description="CarbonX API Bridge — BYOK LLM gateway (REST + SSE).",
    )
    parser.add_argument("--version", action="version", version="carbonx-api-bridge %s" % __version__)
    parser.add_argument("--host", help="override server.host from config.json (default 127.0.0.1)")
    parser.add_argument("--port", type=int, help="override server.port from config.json (default 8787)")
    parser.add_argument(
        "--token",
        help="extra admin token (added to auth.tokens as user 'you', tier 'admin'); "
        "use when you do not want to edit config.json just to add a token",
    )
    args = parser.parse_args(argv)

    created, generated_token = cfg_mod.ensure_config()
    if created:
        print("=" * 62)
        print("[bridge] No config.json found — I created one for you.")
        print("[bridge]")
        print("[bridge] Your access token (paste this into your Lumen/bridge settings):")
        print("[bridge]")
        print("[bridge]   %s" % generated_token)
        print("[bridge]")
        print("[bridge] The bridge is running on the built-in mock AI right now.")
        print("[bridge] To use a real model: open config.json, paste your API key")
        print("[bridge] into one of the providers, then restart.")
        print("[bridge] (For example:      \"openai\": { \"api_key\": \"sk-your-key-here\" })")
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

    server = create_server(cfg, host=args.host, port=args.port)
    host, port = server.server_address[:2]
    print("[bridge] carbonx-api-bridge v%s" % __version__)
    print("[bridge] listening on http://%s:%s  (127.0.0.1 = THIS machine, always)" % (host, port))
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