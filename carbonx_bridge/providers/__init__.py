"""Provider registry + factory.

Adapters are built from config at startup and reused for the life of the
process. Each configured provider is a distinct object so the same vendor
account can be exposed under multiple names (e.g. a premium and a budget
endpoint) with independent budgets.
"""

from __future__ import annotations

from ..errors import BadRequest, ConfigError
from .anthropic import Anthropic
from .base import Provider  # noqa: F401  (re-export for tests)
from .custom import CustomProvider
from .mock import Mock
from .opencode import OpenCode
from .openrouter import OpenRouter
from .openai_compatible import OpenAICompatible

FACTORY = {
    "openai": OpenAICompatible,
    "anthropic": Anthropic,
    "openrouter": OpenRouter,
    "opencode": OpenCode,
    "custom": CustomProvider,
    "other": CustomProvider,
    "mock": Mock,
}


def build_provider(name, cfg):
    ptype = cfg.get("type", "openai")
    cls = FACTORY.get(ptype)
    if cls is None:
        raise ConfigError("unknown provider type '%s'" % (ptype,))
    return cls(name, cfg)


def build_all(providers_cfg):
    return {
        name: build_provider(name, cfg)
        for name, cfg in (providers_cfg or {}).items()
    }


def pick(registry, name_or_none, default_provider):
    """Resolve ``provider`` name (client-supplied) to a registered adapter.

    Client-supplied names are validated against the registry; anything
    else is a 400 with the list of valid names.
    """
    if not name_or_none:
        name_or_none = default_provider
    provider = registry.get(name_or_none)
    if provider is None:
        raise BadRequest(
            "unknown provider '%s'; available: %s"
            % (name_or_none, ", ".join(sorted(registry)) or "(none)")
        )
    return provider