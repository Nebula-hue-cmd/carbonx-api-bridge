"""Authentication and rate-limit tests."""

from __future__ import annotations

import unittest

from carbonx_bridge.auth import Authenticator
from carbonx_bridge.errors import Forbidden, RateLimited, Unauthorized

BASE_AUTH = {
    "tokens": {
        "tok-admin": {"user": "alice", "tier": "admin"},
        "tok-free": {"user": "bob", "tier": "free"},
    },
    "rate_limits": {
        "anon": {"rpm": 1, "rpd": 2},
        "free": {"rpm": 1, "rpd": 2},
        "premium": {"rpm": 50, "rpd": 1000},
        "admin": {"rpm": 50, "rpd": 1000},
    },
}


def make(require_auth=True, allow_anon=False):
    return Authenticator(BASE_AUTH, {"require_auth": require_auth, "allow_anon": allow_anon})


class TestAuthenticator(unittest.TestCase):
    def test_known_token_resolves_user_and_tier(self):
        a = make()
        ctx = a.authenticate("Bearer tok-admin", "1.1.1.1")
        self.assertEqual(ctx.user, "alice")
        self.assertEqual(ctx.tier, "admin")
        self.assertFalse(ctx.anon)

    def test_missing_auth_401_when_required(self):
        a = make()
        with self.assertRaises(Unauthorized):
            a.authenticate(None, "1.1.1.1")

    def test_invalid_token_401_invalid_token(self):
        a = make()
        with self.assertRaises(Unauthorized) as cm:
            a.authenticate("Bearer wrong", "1.1.1.1")
        self.assertEqual(cm.exception.typ, "invalid_token")

    def test_bad_scheme_rejected(self):
        a = make()
        with self.assertRaises(Unauthorized):
            a.authenticate("Basic abc123", "1.1.1.1")

    def test_anon_allowed_when_enabled(self):
        a = make(allow_anon=True)
        ctx = a.authenticate("Bearer wrong", "1.1.1.1")
        self.assertTrue(ctx.anon)
        self.assertEqual(ctx.tier, "anon")
        ctx2 = a.authenticate(None, "2.2.2.2")
        self.assertTrue(ctx2.anon)

    def test_anon_forbidden_without_allow_anon(self):
        a = make()
        with self.assertRaises(Unauthorized):
            a.authenticate("Bearer nope", "1.1.1.1")

    def test_authorize_tiers_gate(self):
        a = make()
        ctx = a.authenticate("Bearer tok-free", "1.1.1.1")
        a.authorize_tiers(ctx, ("free", "admin"))
        ctx2 = a.authenticate("Bearer tok-free", "1.1.1.1")
        with self.assertRaises(Forbidden):
            a.authorize_tiers(ctx2, ("admin",))

    def test_rate_limit_by_tier_and_identity(self):
        a = make()
        # free tier rpm=1: first passes, second 429
        ctx = a.authenticate("Bearer tok-free", "1.1.1.1")
        a.check_rate(ctx, "1.1.1.1")
        with self.assertRaises(RateLimited) as cm:
            a.check_rate(ctx, "1.1.1.1")
        self.assertGreaterEqual(cm.exception.retry_after, 1)

    def test_rate_limit_is_per_user_not_per_tier(self):
        a = make()
        alice = a.authenticate("Bearer tok-admin", "1.1.1.1")
        bob = a.authenticate("Bearer tok-free", "2.2.2.2")
        a.check_rate(alice, "1.1.1.1")
        a.check_rate(bob, "2.2.2.2")  # different identity -> its own bucket
        self.assertIsNone(None)

    def test_anon_keyed_by_ip(self):
        a = make(allow_anon=True)
        c1 = a.authenticate(None, "9.9.9.9")
        c2 = a.authenticate(None, "8.8.8.8")
        a.check_rate(c1, "9.9.9.9")
        a.check_rate(c2, "8.8.8.8")  # different IP buckets
        self.assertTrue(c1.anon)


if __name__ == "__main__":
    unittest.main()