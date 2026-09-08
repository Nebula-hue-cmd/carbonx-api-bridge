"""Request-limit validation tests."""

from __future__ import annotations

import unittest

from carbonx_bridge.errors import BadRequest, PayloadTooLarge
from carbonx_bridge.limits import Validator, parse_json

LIMITS = {
    "max_body_bytes": 1024,
    "max_prompt_chars": 64,
    "max_history_turns": 3,
    "max_files": 2,
    "max_file_bytes": 128,
    "max_docs_chars": 256,
    "max_response_tokens": 32,
}


def payload(messages=None, **kw):
    p = {"messages": messages if messages is not None else [{"role": "user", "content": "hi"}]}
    p.update(kw)
    return p


class TestValidator(unittest.TestCase):
    def setUp(self):
        self.v = Validator(LIMITS)

    def test_accepts_minimal(self):
        self.v.validate_chat_payload(payload())

    def test_messages_must_be_array(self):
        with self.assertRaises(BadRequest):
            self.v.validate_chat_payload({"messages": "nope"})

    def test_empty_messages_rejected(self):
        with self.assertRaisesRegex(BadRequest, "must not be empty"):
            self.v.validate_chat_payload(payload([]))

    def test_too_many_turns_rejected(self):
        msgs = [{"role": "user", "content": "x"} for _ in range(50)]
        with self.assertRaisesRegex(BadRequest, "turns"):
            self.v.validate_chat_payload(payload(msgs))

    def test_invalid_role_rejected(self):
        with self.assertRaisesRegex(BadRequest, "role"):
            self.v.validate_chat_payload(payload([{"role": "elf", "content": "x"}]))

    def test_non_string_content_rejected(self):
        with self.assertRaisesRegex(BadRequest, "content"):
            self.v.validate_chat_payload(payload([{"role": "user", "content": 12}]))

    def test_prompt_budget_enforced(self):
        msgs = [{"role": "user", "content": "a" * 30}, {"role": "assistant", "content": "b" * 40}]
        with self.assertRaisesRegex(BadRequest, "prompt is 70 characters"):
            self.v.validate_chat_payload(payload(msgs))

    def test_body_size_cap(self):
        with self.assertRaises(PayloadTooLarge):
            self.v.check_body_size(b"x" * 2000)

    def test_files_count_and_size(self):
        self.v.validate_files([{"name": "a", "size": 10}, {"name": "b", "size": 20}])
        with self.assertRaisesRegex(BadRequest, "entries"):
            self.v.validate_files([{}, {}, {}])
        with self.assertRaisesRegex(BadRequest, "size"):
            self.v.validate_files([{"name": "big", "size": 9999}])

    def test_docs_budget(self):
        self.v.validate_docs(["a" * 100, {"content": "b" * 100}])
        with self.assertRaisesRegex(BadRequest, "characters"):
            self.v.validate_docs(["a" * 1000])

    def test_max_tokens_must_be_positive(self):
        with self.assertRaisesRegex(BadRequest, "max_tokens"):
            self.v.validate_chat_payload(payload(max_tokens=-3))

    def test_parse_json_errors(self):
        with self.assertRaises(BadRequest):
            parse_json(b"{not json")
        with self.assertRaises(BadRequest):
            parse_json(b"\xff\xfe")


if __name__ == "__main__":
    unittest.main()