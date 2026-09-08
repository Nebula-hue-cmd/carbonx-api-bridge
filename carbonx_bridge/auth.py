"""Server-side authentication and per-tier rate limiting.

Security model
--------------
* The client is NEVER trusted to name its user, role, or plan. Identity
  comes only from the ``Authorization: Bearer <token>`` header, which the
  server maps to a configured user + tier (``auth.tokens``).
* ``server.require_auth`` defaults to ``True``. When ``server.allow_anon``
  is additionally enabled, missing/invalid tokens are treated as the
  ``anon`` tier, keyed by source IP.
* Rate limits are enforced server-side per (tier, identity) bucket. Counts
  live in memory; a restart resets them (documented).

Tokens in config are compared with a constant-time comparison.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import defaultdict, deque

from .errors import RateLimited, Unauthorized, Forbidden
from . import config


def _constant_eq(a, b):
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


class AuthContext:
    """Resolved identity for a single request."""

    def __init__(self, user=None, tier=None, anon=False, token_hash=None):
        self.user = user
        self.tier = tier
        self.anon = anon
        self.token_hash = token_hash

    def rate_key(self, remote_ip):
        if self.anon:
            return "anon|%s" % remote_ip
        return "%s|%s" % (self.tier, self.user)


class Authenticator:
    """Maps bearer tokens to users/tiers and enforces rate limits."""

    def __init__(self, auth_cfg, server_cfg):
        self.tokens = {}
        for raw, info in (auth_cfg.get("tokens") or {}).items():
            self.tokens[config.resolve(raw)] = {
                "user": info["user"],
                "tier": info.get("tier", "free"),
            }
        self.rate_limits = dict(auth_cfg.get("rate_limits") or {})
        self.require_auth = bool(server_cfg.get("require_auth", True))
        self.allow_anon = bool(server_cfg.get("allow_anon", False))
        self._buckets = defaultdict(deque)

    def authenticate(self, authorization_header, remote_ip=None):
        """Resolve the request identity or raise 401/403.

        ``authorization_header`` is the raw ``Authorization`` header value
        (possibly None). Returns an AuthContext.
        """
        if authorization_header:
            parts = authorization_header.split(None, 1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                token = parts[1].strip()
                entry = self._lookup(token)
                if entry is None:
                    if not self.allow_anon:
                        raise Unauthorized("invalid token", typ="invalid_token")
                    return AuthContext(tier="anon", anon=True)
                return AuthContext(
                    user=entry["user"],
                    tier=entry["tier"],
                    token_hash=self._hash(token),
                )
            if self.allow_anon:
                return AuthContext(tier="anon", anon=True)
            raise Unauthorized("unsupported Authorization scheme", typ="invalid_auth")

        if self.require_auth and not self.allow_anon:
            raise Unauthorized("authentication required", typ="auth_required")
        if self.allow_anon:
            return AuthContext(tier="anon", anon=True)
        return AuthContext()  # auth disabled server-wide

    def authorize_tiers(self, ctx, allowed):
        """Raise 403 if the context tier is outside ``allowed``."""
        if ctx.tier not in allowed:
            raise Forbidden(
                "tier '%s' is not allowed for this endpoint (needs one of: %s)"
                % (ctx.tier or "none", ", ".join(allowed))
            )
        return True

    def check_rate(self, ctx, remote_ip):
        """Enforce per-tier rpm/rpd buckets; raise 429 when exhausted."""
        key = ctx.rate_key(remote_ip)
        limits = self.rate_limits.get(ctx.tier or "free", {})
        rpm = int(limits.get("rpm", 0) or 0)
        rpd = int(limits.get("rpd", 0) or 0)
        now = time.time()

        def _bump(span, budget):
            if not budget:
                return None
            bucket = self._buckets[key + "|%d" % span]
            while bucket and now - bucket[0] >= span:
                bucket.popleft()
            if len(bucket) >= budget:
                oldest = bucket[0] if bucket else now
                return max(1, int((oldest + span) - now))
            bucket.append(now)
            return None

        retry = _bump(60, rpm)
        if retry is None and rpd:
            retry = _bump(86400, rpd)
        if retry is not None:
            raise RateLimited("rate limit exceeded for tier '%s'" % (ctx.tier or "free"), retry_after=retry)

    @staticmethod
    def _hash(token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]

    def _lookup(self, token):
        for candidate, entry in self.tokens.items():
            if _constant_eq(candidate, token):
                return entry
        return None