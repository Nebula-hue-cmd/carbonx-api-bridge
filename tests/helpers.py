"""Shared helpers for the bridge test-suite."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.request

from carbonx_bridge import config as cfg_mod
from carbonx_bridge.server import create_server


def make_config(overrides=None, tmpdir=None):
    cfg = {
        "server": {
            "host": "127.0.0.1",
            "port": 0,
            "require_auth": True,
            "allow_anon": False,
        },
        "auth": {
            "tokens": {"test-token-admin": {"user": "alice", "tier": "admin"}},
            "rate_limits": {
                "anon": {"rpm": 2, "rpd": 5},
                "free": {"rpm": 2, "rpd": 5},
                "premium": {"rpm": 100, "rpd": 1000},
                "admin": {"rpm": 100000, "rpd": 100000},
            },
        },
        "limits": {
            "max_body_bytes": 4096,
            "max_prompt_chars": 512,
            "max_history_turns": 5,
            "max_files": 2,
            "max_file_bytes": 1024,
            "max_docs_chars": 2048,
            "max_response_tokens": 64,
        },
        "default_provider": "mock",
        "providers": {
            "mock": {
                "type": "mock",
                "default_model": "mock/m1",
                "reply": "hello from mock",
                "stream_pieces": ["delta-one ", "delta-two"],
            }
        },
    }
    # merge overrides shallowly enough for tests
    def merge(base, extra):
        for k, v in (extra or {}).items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                merge(base[k], v)
            else:
                base[k] = v

    merge(cfg, overrides)
    return cfg


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2)


class TempConfigDir:
    """Context manager: writes config.json for tests that load through config."""

    def __init__(self, cfg):
        self.cfg = cfg

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cbridge-")
        path = os.path.join(self.tmp.name, "config.json")
        write_json(path, self.cfg)
        self.path = path
        return self

    def __exit__(self, *exc):
        self.tmp.cleanup()
        return False


class RunningServer:
    """Start a real bridge server on an ephemeral port; gives the base URL."""

    def __init__(self, cfg):
        server = create_server(cfg)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.server = server
        self.url = "http://127.0.0.1:%d" % server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    # -- client helpers ------------------------------------------------
    def request(self, path, method=None, body=None, token=None, headers=None):
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=15)
        all_headers = dict(headers or {})
        if token is not None:
            all_headers["Authorization"] = "Bearer " + token
        raw = None
        if body is not None:
            raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        method = method or ("POST" if raw is not None else "GET")
        conn.request(method, path, body=raw, headers=all_headers)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res.status, res.headers, data

    def json(self, path, method=None, body=None, token=None, headers=None):
        status, headers, data = self.request(path, method, body, token, headers)
        return status, json.loads(data.decode("utf-8")) if data else {}


class BridgeTest(unittest.TestCase):
    """Base case: starts one server per test method."""

    def setUp(self):
        cfg = make_config()
        cfg["server"]["port"] = 0
        self.server = RunningServer(cfg)
        self.token_admin = "test-token-admin"

    def tearDown(self):
        self.server.close()


def request(url, method="POST", payload=None, token=None, timeout=15):
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as ex:
        return ex.code, ex.read()