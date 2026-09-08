"""Bridge API errors with stable machine-readable types.

Every HTTP error response on the wire looks like:

    {"error": {"type": "...", "message": "...", "code": 4xx/5xx}}

The ``code`` always mirrors the real HTTP status so clients can react
without parsing text.
"""

from __future__ import annotations


class ApiError(Exception):
    """An error that maps 1:1 to a clean HTTP response.

    ``type`` is a stable machine-readable slug (see ERROR_TYPES). Errors
    raised anywhere in the request path that are *not* ApiError become a
    500 ``internal_error`` (with the message redacted before it is sent).
    """

    def __init__(self, typ: str, message: str, code: int = 400):
        super().__init__(message)
        self.typ = typ
        self.message = message
        self.code = code

    def as_dict(self) -> dict:
        return {
            "error": {
                "type": self.typ,
                "message": self.message,
                "code": self.code,
            }
        }


class BadRequest(ApiError):
    def __init__(self, message, typ="bad_request"):
        super().__init__(typ, message, 400)


class Unauthorized(ApiError):
    def __init__(self, message, typ="auth_required"):
        super().__init__(typ, message, 401)


class Forbidden(ApiError):
    def __init__(self, message, typ="forbidden"):
        super().__init__(typ, message, 403)


class NotFound(ApiError):
    def __init__(self, message="not found", typ="not_found"):
        super().__init__(typ, message, 404)


class MethodNotAllowed(ApiError):
    def __init__(self, message="method not allowed", typ="method_not_allowed"):
        super().__init__(typ, message, 405)


class PayloadTooLarge(ApiError):
    def __init__(self, message, typ="payload_too_large"):
        super().__init__(typ, message, 413)


class RateLimited(ApiError):
    def __init__(self, message, retry_after=1, typ="rate_limited"):
        super().__init__(typ, message, 429)
        self.retry_after = retry_after


class UpstreamError(ApiError):
    def __init__(self, message, code=502, typ="upstream_error"):
        super().__init__(typ, message, code)


class StreamingUnsupported(ApiError):
    def __init__(self, message, typ="streaming_unsupported"):
        super().__init__(typ, message, 400)


class ConfigError(ApiError):
    def __init__(self, message):
        super().__init__("config_error", message, 500)


#: Only these status codes are ever emitted to clients.
USED_STATUSES = (400, 401, 403, 404, 405, 413, 429, 500, 502)