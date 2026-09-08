"""Secret redaction tests.

Every test here asserts the ORIGINAL secret never survives; the whole point
of the module. A regression means an original leaked into a log/error.
"""

from __future__ import annotations

import unittest

from carbonx_bridge.redact import REDACTED, redact, redact_value

CASES = [
    "key is sk-abcdef1234567890XYZ",
    "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz123456",
    "ANTHROPIC_API_KEY=sk-ant-api03-abcd1234EFGH5678ijklmnopQRST",
    "Authorization: Bearer ghp_ABCDefghijklmnopQRSTuvwxyz12345",
    "x-opencode-session: 19e8ff0f7c1a4b2d9f3e6a8b0c1d2e3f",
    "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE",
    "dsn mongodb+srv://user:supersecret@cluster0.example.net/db",
    "pg postgresql://admin:hunter2@db.example.com:5432/app",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----",
    'config "api_key": "sk-ant-api03-zzzzzzzzzzzzzzzzzzzzzzzzzzzz"',
]


class TestRedact(unittest.TestCase):
    def test_each_secret_class_is_removed(self):
        for case in CASES:
            out = redact(case)
            self.assertIn(REDACTED, out, "no redaction marker for: %r" % case)

    def test_originals_never_survive(self):
        for case in CASES:
            out = redact(case)
            # Cheap leakage probe: nothing that looks like the secret remains.
            import re

            for frag in re.split(r"[^A-Za-z0-9_/:.@<>-]+", case):
                frag = frag.strip('"')
                if len(frag) >= 12 and frag not in (
                    "mongodb+srv://",
                    "postgresql://",
                    "BEGIN RSA PRIVATE",
                    "END RSA PRIVATE",
                ):
                    self.assertNotIn(frag, out, "original fragment leaked for %r" % case)

    def test_plain_text_passes_through(self):
        self.assertEqual(redact("hello world"), "hello world")
        self.assertEqual(redact(123), "123")

    def test_redact_value_deep_copies(self):
        obj = {"messages": ["use sk-abcdef1234567890XYZ now"], "n": 42, "ok": True, "nested": {"k": "ghp_ABCDefghijklmnopQRSTuvwxyz12345"}}
        out = redact_value(obj)
        self.assertEqual(out["n"], 42)
        self.assertEqual(out["ok"], True)
        self.assertIn(REDACTED, out["messages"][0])
        self.assertNotIn("sk-abcdef1234567890XYZ", out["messages"][0])
        self.assertNotIn("ghp_ABCDefghijklmnopQRSTuvwxyz12345", out["nested"]["k"])
        self.assertIn(REDACTED, out["nested"]["k"])
        self.assertIsNot(obj, out)


if __name__ == "__main__":
    unittest.main()