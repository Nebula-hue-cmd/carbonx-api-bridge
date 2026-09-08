"""SSE framing and streaming transport helpers.

The bridge never fakes streaming: if a provider cannot stream, the
``/v1/stream`` endpoint fails with 400 ``streaming_unsupported`` and tells
the client to use ``/v1/chat`` instead.

Wire format (unchanged end-to-end): standard ``text/event-stream``.
* chat chunks   -> ``data: {"type":"content","delta":"...","model":...,"index":n}``
* usage         -> ``data: {"type":"usage","usage":{...}}``
* warnings      -> ``data: {"type":"warning","message":"..."}``
* terminal      -> ``data: [DONE]``
* fatal errors  -> ``data: {"type":"error","error":{"type":...,"message":...}}``
"""

from __future__ import annotations

import json
import time

from .redact import redact
from .errors import ApiError, UpstreamError


def parse_sse(raw_byte_lines):
    """Yield ``(event, payload)`` pairs from raw SSE line fragments.

    Handles both the OpenAI/OpenRouter style (bare ``data:`` lines) and the
    Anthropic style (``event:`` + ``data:`` pairs). Comments (``:`` lines)
    and heartbeat lines are skipped. A ``data: [DONE]`` line yields
    ``(None, None)`` to signal termination.
    """
    current = None
    for raw in raw_byte_lines:
        if not raw:
            continue
        line = raw.decode("utf-8", "replace").strip("\r\n")
        if line.startswith("event:"):
            current = line[6:].strip() or None
        elif line.startswith("data:"):
            payload = line[5:].strip()
            if payload == "[DONE]":
                yield (None, None)
                return
            if payload:
                yield (current, payload)
            current = None
        # everything else (comments, keep-alives) is ignored


def encode_chunk(chunk: dict) -> bytes:
    return ("data: " + json.dumps(chunk, ensure_ascii=False, separators=(",", ":")) + "\n\n").encode("utf-8")


def heartbeat() -> bytes:
    return b": keep-alive\n\n"


def done() -> bytes:
    return b"data: [DONE]\n\n"


def error_chunk(err: ApiError) -> bytes:
    return encode_chunk({"type": "error", "error": err.as_dict()["error"]})


def stream_provider_events(provider, params, emit, heartbeat_every=15.0):
    """Run a provider stream, emitting framed events through ``emit(bytes)``.

    Returns the provider's final summary dict on success. Provider-side
    exceptions are converted to clean upstream errors and emitted as an
    SSE error event before returning, so the client always sees a
    well-formed stream.
    """
    started = time.time()
    last_flush = time.time()

    def _keepalive():
        nonlocal last_flush
        now = time.time()
        if now - last_flush >= heartbeat_every:
            emit(heartbeat())
            last_flush = now

    def _emit_text(fragment):
        nonlocal last_flush
        emit(encode_chunk({"type": "content", "delta": fragment}))
        last_flush = time.time()

    try:
        final = provider.stream(params, _emit_text)
    except Exception as ex:  # noqa: BLE001 - normalize everything
        if isinstance(ex, ApiError):
            emit(error_chunk(ex))
        else:
            emit(
                error_chunk(
                    UpstreamError(
                        "upstream_stream_error: %s: %s"
                        % (type(ex).__name__, redact(str(ex)))
                    )
                )
            )
        return None
    finally:
        _keepalive()

    if params.get("include_usage", True) and final and final.get("usage"):
        emit(encode_chunk({"type": "usage", "usage": final["usage"]}))
    return final