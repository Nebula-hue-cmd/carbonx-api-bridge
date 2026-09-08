"""Configuration loading for the CarbonX API bridge.

Consistent with the Lumen bridge project conventions:

* ``config.json`` holds the real secrets (or ``env:VAR`` references).
* ``config.example.json`` is the committed template with placeholders only.
* Every key the server needs has a validated default, so hand-written
  configs stay small.

Secrets loaded here are resolved once at startup and are ONLY ever stored
in the in-memory ``CONFIG``; they are never written to logs or responses
(see :mod:`carbonx_bridge.redact`).
"""

from __future__ import annotations

import json
import os
import secrets
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# When frozen (portable .exe via PyInstaller) the package dir is a temp
# extraction folder, so user files live NEXT TO the exe instead of in the
# repo root. "var:NAME" resolves relative to THAT location.
if getattr(sys, "frozen", False):
    PROJECT_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    PROJECT_DIR = os.path.dirname(HERE)
DEFAULT_CONFIG_PATH = os.path.join(PROJECT_DIR, "config.json")

DEFAULT_CONFIG = {
    "server": {
        "host": "127.0.0.1",
        "port": 8787,
        "require_auth": True,
        "allow_anon": False,
    },
    "auth": {
        # token string -> {"user": id, "tier": "admin"|"premium"|"free"}
        "tokens": {},
        "rate_limits": {
            "anon": {"rpm": 10, "rpd": 200},
            "free": {"rpm": 30, "rpd": 1000},
            "premium": {"rpm": 120, "rpd": 5000},
            "admin": {"rpm": 1000, "rpd": 100000},
        },
    },
    "limits": {
        "max_body_bytes": 262144,  # 256 KiB
        "max_prompt_chars": 8000,
        "max_history_turns": 40,
        "max_files": 5,
        "max_file_bytes": 65536,  # 64 KiB per declared attachment
        "max_docs_chars": 40000,
        "max_response_tokens": 2048,
    },
    "default_provider": "mock",
    "providers": {},
}

VALID_PROVIDER_TYPES = (
    "openai",
    "anthropic",
    "openrouter",
    "opencode",
    "custom",
    "other",
    "mock",
)
VALID_TIERS = ("admin", "premium", "free", "anon")


def deep_merge(base, override):
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def resolve(value, env=None):
    """Turn ``env:NAME`` into the environment value; everything else passes."""
    if isinstance(value, str) and value.startswith("env:"):
        name = value[4:]
        source = os.environ if env is None else env
        if name not in source:
            raise ValueError("environment variable '%s' referenced in config but not set" % name)
        return source[name]
    return value


def load_dotenv(path=None):
    path = path or os.path.join(PROJECT_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def load_config(path=None, env=None):
    """Load, merge with defaults, and validate configuration.

    ``path`` defaults to ``config.json`` next to the package root.
    Returns a fully-populated config dict.
    """
    path = path or DEFAULT_CONFIG_PATH
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8-sig") as fh:
            user = json.load(fh)
    else:
        user = {}

    cfg = deep_merge(json.loads(json.dumps(DEFAULT_CONFIG)), user)

    # ---- validate ----
    if not cfg["providers"]:
        raise ValueError("config.json must define at least one provider")

    unknown = [
        name
        for name, pc in cfg["providers"].items()
        if pc.get("type", "openai") not in VALID_PROVIDER_TYPES
    ]
    if unknown:
        raise ValueError(
            "unknown provider type(s): %s (allowed: %s)"
            % (", ".join(unknown), ", ".join(VALID_PROVIDER_TYPES))
        )

    default_provider = cfg.get("default_provider") or list(cfg["providers"])[0]
    if default_provider not in cfg["providers"]:
        raise ValueError(
            "default_provider '%s' is not defined in providers" % default_provider
        )
    cfg["default_provider"] = default_provider

    tiers = set(cfg["auth"].get("rate_limits") or {})
    unknown_tiers = tiers - set(VALID_TIERS)
    if unknown_tiers:
        raise ValueError("unknown rate-limit tier(s): %s" % ", ".join(sorted(unknown_tiers)))

    for token, info in (cfg["auth"].get("tokens") or {}).items():
        if not isinstance(info, dict) or "user" not in info:
            raise ValueError("auth.tokens entries must be objects with a 'user'")
        tier = info.get("tier", "free")
        if tier not in VALID_TIERS:
            raise ValueError("unknown tier '%s' for token (user %s)" % (tier, info["user"]))

    return cfg


# Module-level singleton matching bridge_server.py conventions:
#   import carbonx_bridge.config as config
#   config.CONFIG["server"]["port"]
CONFIG = None


def make_default_config(token):
    """A ready-to-run skeleton config, used when config.json is missing.

    The only provider that works with zero edits is ``mock``, so the bridge
    always starts. ``api_key`` fields are left empty for the user to fill;
    editing one of the placeholders is the *only* manual step (or delete the
    mock provider and set ``default_provider`` to the one you edited).
    """
    return {
        "server": {
            "host": "127.0.0.1",
            "port": 8787,
            "require_auth": True,
            "allow_anon": False,
        },
        "auth": {
            "tokens": {token: {"user": "you", "tier": "admin"}},
            "rate_limits": {
                "anon": {"rpm": 10, "rpd": 200},
                "free": {"rpm": 30, "rpd": 1000},
                "premium": {"rpm": 120, "rpd": 5000},
                "admin": {"rpm": 1000, "rpd": 100000},
            },
        },
        "limits": {
            "max_body_bytes": 262144,
            "max_prompt_chars": 8000,
            "max_history_turns": 40,
            "max_files": 5,
            "max_file_bytes": 65536,
            "max_docs_chars": 40000,
            "max_response_tokens": 2048,
        },
        "default_provider": "mock",
        "providers": {
            "mock": {
                "type": "mock",
                "default_model": "mock/m1",
                "reply": "This is the mock provider. Put a real API key in config.json and pick a provider to talk to a real model.",
            },
            "openai": {
                "type": "openai",
                "api_key": "",
                "default_model": "gpt-4o-mini",
            },
            "claude": {
                "type": "anthropic",
                "api_key": "",
                "default_model": "claude-3-5-sonnet-latest",
            },
            "openrouter": {
                "type": "openrouter",
                "api_key": "",
                "default_model": "openai/gpt-4o-mini",
            },
            "cursor": {
                "type": "custom",
                "api_style": "anthropic",
                "base_url": "http://localhost:3000",
                "default_model": "cursor-fast",
            },
        },
    }


def ensure_config(path=None):
    """Create config.json (with a fresh random admin token) if it is missing.

    Returns ``(created, token)`` where ``token`` is the generated admin token
    when a file was created (``None`` otherwise). Never overwrites an
    existing file, and never writes secrets anywhere but the file itself.
    """
    path = path or DEFAULT_CONFIG_PATH
    if os.path.exists(path):
        return False, None
    token = secrets.token_urlsafe(24)
    cfg = make_default_config(token)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return True, token


def init(path=None, env=None):
    global CONFIG
    load_dotenv()
    CONFIG = load_config(path=path, env=env)
    return CONFIG


def get(*path_items, default=None):
    node = CONFIG
    for key in path_items:
        node = (node or {}).get(key)
        if node is None:
            return default
    return node