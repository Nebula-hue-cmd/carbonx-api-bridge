"""Server-side request validation.

The bridge enforces hard caps on every inbound request BEFORE any provider
is touched, so a misbehaving or hostile client cannot exhaust upstream
budgets. All limits come from the server's ``limits`` config — never from
the client.

Two limits explode into distinct HTTP codes on purpose:
* body size            -> 413 payload_too_large
* everything else      -> 400 invalid_request (with the offending field)
"""

from __future__ import annotations

import json

from .errors import BadRequest, PayloadTooLarge


class Validator:
    def __init__(self, limits_cfg):
        self.cfg = limits_cfg or {}

    @property
    def max_body_bytes(self):
        return int(self.cfg.get("max_body_bytes") or 0) or None

    def check_body_size(self, raw_body: bytes):
        cap = self.max_body_bytes
        if cap and len(raw_body) > cap:
            raise PayloadTooLarge(
                "request body of %d bytes exceeds limit of %d bytes"
                % (len(raw_body), cap)
            )

    def _max(self, key, fallback):
        val = self.cfg.get(key)
        return val if isinstance(val, int) else fallback

    def validate_chat_payload(self, payload: dict):
        """Validate the normalized request for /v1/chat and /v1/stream."""
        if not isinstance(payload, dict):
            raise BadRequest("request body must be a JSON object", typ="invalid_request")

        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise BadRequest("'messages' must be a JSON array", typ="invalid_request")
        if not messages:
            raise BadRequest("'messages' must not be empty", typ="invalid_request")

        max_turns = self._max("max_history_turns", 40)
        if len(messages) > max_turns:
            raise BadRequest(
                "'messages' has %d turns; limit is %d" % (len(messages), max_turns),
                typ="invalid_request",
            )

        # Structural validation + prompt budget. Only role/content are
        # meaningful to providers; everything else is ignored upstream.
        budget = self._max("max_prompt_chars", 8000)
        total = 0
        for idx, msg in enumerate(messages):
            if not isinstance(msg, dict):
                raise BadRequest("message[%d] must be an object" % idx, typ="invalid_request")
            role = msg.get("role")
            if role not in ("system", "user", "assistant", "tool"):
                raise BadRequest(
                    "message[%d].role must be one of system/user/assistant/tool" % idx,
                    typ="invalid_request",
                )
            content = msg.get("content")
            if not isinstance(content, str):
                raise BadRequest(
                    "message[%d].content must be a string" % idx, typ="invalid_request"
                )
            total += len(content)
        if total > budget:
            raise BadRequest(
                "combined prompt is %d characters; limit is %d" % (total, budget),
                typ="invalid_request",
            )

        if payload.get("system") is not None and not isinstance(payload["system"], str):
            raise BadRequest("'system' must be a string", typ="invalid_request")

        files = payload.get("files")
        if files is not None:
            self.validate_files(files)

        docs = payload.get("docs")
        if docs is not None:
            self.validate_docs(docs)

        max_cap = self._max("max_response_tokens", 2048)
        if payload.get("max_tokens") is not None:
            mt = payload["max_tokens"]
            if not isinstance(mt, (int, float)) or mt <= 0:
                raise BadRequest("'max_tokens' must be a positive number", typ="invalid_request")

    def validate_files(self, files):
        if not isinstance(files, list):
            raise BadRequest("'files' must be an array", typ="invalid_request")
        max_files = self._max("max_files", 5)
        if len(files) > max_files:
            raise BadRequest("'files' has %d entries; limit is %d" % (len(files), max_files))
        cap = self._max("max_file_bytes", 65536)
        for idx, f in enumerate(files):
            if not isinstance(f, dict):
                raise BadRequest("files[%d] must be an object" % idx)
            size = f.get("size")
            if isinstance(size, (int, float)) and size > cap:
                raise BadRequest(
                    "files[%d].size of %d bytes exceeds limit of %d bytes"
                    % (idx, size, cap)
                )

    def validate_docs(self, docs):
        if not isinstance(docs, list):
            raise BadRequest("'docs' must be an array", typ="invalid_request")
        cap = self._max("max_docs_chars", 40000)
        total = 0
        for idx, d in enumerate(docs):
            if isinstance(d, str):
                total += len(d)
            elif isinstance(d, dict):
                total += len(d.get("content") or "")
            else:
                raise BadRequest("docs[%d] must be a string or object" % idx)
        if total > cap:
            raise BadRequest(
                "docs total of %d characters exceeds limit of %d" % (total, cap)
            )


def parse_json(raw_body: bytes):
    """Decode a request body; raises BadRequest(400) on malformed JSON."""
    try:
        return json.loads(raw_body.decode("utf-8"))
    except UnicodeDecodeError:
        raise BadRequest("request body must be UTF-8", typ="invalid_request")
    except json.JSONDecodeError:
        raise BadRequest("invalid JSON body", typ="invalid_request")