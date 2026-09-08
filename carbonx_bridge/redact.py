"""Secret redaction.

Everything that can leave the process in a log line, an error message, or a
response mirrors only *redacted* views of secrets. The originals are never
logged, never echoed, and never serialized.

Rule: if you do not know a value cannot be a secret, route it through
``redact()`` (strings) or ``redact_value()`` (nested structures) before it
reaches a log or response.
"""

from __future__ import annotations

import re

_NOT_IN_STR = "(?:x|X|0|_){0,4}"  # tolerant of typical placeholders

REDACT_RE = re.compile(
    r"(?i)"
    r"(?:"
    r"sk-[A-Za-z0-9]{12,}"                                     # OpenAI / Anthropic style keys
    r"|sk-(?:proj|ant|plain|svcacct)-[A-Za-z0-9_-]{8,}"        # prefixed service keys
    r"|gh[pousr]_[A-Za-z0-9]{10,}"                             # GitHub PATs / OAuth / user
    r"|github_pat_[A-Za-z0-9_]{10,}"                           # GitHub fine-grained PATs
    r"|AKIA[0-9A-Z]{16}"                                       # AWS key ids
    r"|xox[baprs]-[A-Za-z0-9-]{8,}"                            # Slack tokens
    r"|-----BEGIN(?: [A-Z0-9 ]+)? PRIVATE KEY-----[\s]*[A-Za-z0-9+/=\s]*?-----END(?: [A-Z0-9 ]+)? PRIVATE KEY-----"            # PEM private keys
    r"|mongodb\+srv://[^@\s/]+:[^@\s]+@"                       # Mongo connection strings
    r"|(?:postgres|postgresql|mysql|mariadb|redis|rediss|amqp|amqps)://[^@\s]+:[^@\s]+@"
    r"|Authorization\s*[:=]\s*Bearer\s+\S+"
    r"|x-opencode-session\s*[:=]\s*\S+"
    r"|(?:OPENAI_API_KEY|ANTHROPIC_API_KEY|OPENROUTER_API_KEY|DEEPSEEK_API_KEY"
    r"|AWS_SECRET_ACCESS_KEY|AWS_ACCESS_KEY_ID|AZURE_API_KEY|COHERE_API_KEY"
    r"|GROQ_API_KEY|MISTRAL_API_KEY|TOGETHER_API_KEY|GEMINI_API_KEY"
    r"|REPLICATE_API_TOKEN|JWT_SECRET|API_KEY)\s*=\s*\S+"
    r"|(?:api_key|token|secret|password)\s*[:=]\s*" + _NOT_IN_STR + r'"?[A-Za-z0-9_\-\.\+/]{8,}"?'
    r")",
    re.IGNORECASE,
)

REDACTED = "[REDACTED_SECRET]"


def redact(text) -> str:
    """Return ``text`` with every recognized secret occurrence replaced."""
    if not isinstance(text, str):
        text = str(text)
    return REDACT_RE.sub(REDACTED, text)


def redact_value(value):
    """Return a copy of any JSON-able structure with secrets scrubbed.

    Objects and lists are deep-copied; strings are redacted; scalar
    non-strings pass through unchanged.
    """
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(v) for v in value]
    if isinstance(value, str):
        return redact(value)
    return value