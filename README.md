# CarbonX API Bridge

A **Bring-Your-Own-Key** (BYOK), provider-abstraction HTTP API bridge for LLMs.
Your Lumen scripts, editors, and consoles talk to one stable, minimal JSON API;
the bridge talks to OpenAI, Anthropic, OpenRouter (and any OpenAI-compatible
endpoint such as Ollama) on a per-user, server-side-managed basis.

- **Pure Python 3.10+ standard library** — zero dependencies, runs offline.
- **Secure by default**: auth required, per-tier server-side rate limits,
  hard request caps, and secret redaction at every logging boundary.
- **Honest streaming**: `text/event-stream` for providers that support it;
  providers that cannot stream return `400 streaming_unsupported` (never faked).

```
Lumen / Luau client ── HTTPS/JSON (bearer token) ──> CarbonX API Bridge ──> Provider (BYOK) ──> LLM
```

---

## Quick start

```bash
git clone https://github.com/<you>/carbonx-api-bridge.git
cd carbonx-api-bridge
cp config.example.json config.json     # then edit: tokens, keys, models
python -m carbonx_bridge               # or: pip install -e . && carbonx-bridge
```

Startup prints the bound address, provider list, and the auth posture.
Secrets live **only** in `config.json` / `.env` (via `env:VAR`); they are
never logged and never appear in responses or errors.

### Minimal config

```json
{
  "server": { "host": "127.0.0.1", "port": 8787, "require_auth": true, "allow_anon": false },
  "auth": {
    "tokens": { "REPLACE-WITH-A-LONG-RANDOM-TOKEN": { "user": "you", "tier": "admin" } },
    "rate_limits": {
      "anon": { "rpm": 10, "rpd": 200 },
      "free": { "rpm": 30, "rpd": 1000 },
      "premium": { "rpm": 120, "rpd": 5000 },
      "admin": { "rpm": 1000, "rpd": 100000 }
    }
  },
  "default_provider": "openai",
  "providers": {
    "openai":   { "type": "openai",     "api_key": "env:OPENAI_API_KEY", "default_model": "gpt-4o-mini" },
    "claude":   { "type": "anthropic",  "api_key": "env:ANTHROPIC_API_KEY", "default_model": "claude-3-5-sonnet-latest" },
    "openrouter": { "type": "openrouter", "api_key": "env:OPENROUTER_API_KEY", "default_model": "openai/gpt-4o-mini" }
  }
}
```

Provider keys may also be set inline; `env:NAME` is preferred. Never commit
`config.json`.

---

## Endpoints

| Endpoint | Auth | Description |
|---|---|---|
| `GET /health` | no | Liveness; version, providers, `require_auth`. |
| `GET /v1/models` | yes | `{"objects": [{"provider", "model", "supports_streaming", ...}]}` |
| `POST /v1/chat` | yes | Non-streaming response: `{"id","provider","model","content","usage","finish_reason"}` |
| `POST /v1/stream` | yes | `text/event-stream` (see below). |

`Authorization: Bearer <token>` maps to a configured user + tier. Client
supplied user/role/plan fields are **ignored**.

### Chat

```bash
curl http://127.0.0.1:8787/v1/chat \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"say hi"}],"provider":"openai"}'
```

```json
{
  "id": "chat_1a2b3c4d5e6f",
  "provider": "openai",
  "model": "gpt-4o-mini",
  "content": "Hi there!",
  "usage": { "prompt_tokens": 6, "completion_tokens": 3, "total_tokens": 9 },
  "finish_reason": "stop"
}
```

### Streaming (SSE)

```bash
curl -N http://127.0.0.1:8787/v1/stream \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"count to 3"}],"include_usage":true}'
```

```text
data: {"type":"content","delta":"one"}

data: {"type":"content","delta":" two"}

data: {"type":"content","delta":" three"}

data: {"type":"usage","usage":{"prompt_tokens":6,"completion_tokens":6,"total_tokens":12}}

data: [DONE]
```

Mid-stream errors emit a `data: {"type":"error","error":{...}}` event before
`[DONE]`, so clients always see a well-formed stream.

---

## Provider adapters

| `type` | Wire format | Notes |
|---|---|---|
| `openai` | OpenAI Chat Completions | `base_url` adjustable (Ollama, vLLM, etc.) |
| `anthropic` | Anthropic Messages | `x-api-key` + `anthropic-version`; event/data SSE |
| `openrouter` | OpenAI-compatible | optional `HTTP-Referer` / `X-Title` via `headers` |
| `opencode` | OpenAI-compatible (`https://opencode.ai/zen/go/v1`) | **explicit opt-in** — requires `x_opencode_session` |
| `mock` | none (in-process) | deterministic fake for local testing, CI |

### OpenCode free tier — documented, opt-in, never bypassed

OpenCode's free **Zen Go** tier rejects requests that lack an
`x-opencode-session` header with a `MissingSessionID` error
(verified 2026-09). That tier is meant to be driven from an OpenCode session.
This bridge therefore **refuses to build an `opencode` provider unless you
supply `x_opencode_session`** with a stable session id you already own:

```json
"opencode": {
  "type": "opencode",
  "api_key": "env:OPENROUTER_API_KEY",
  "x_opencode_session": "<your-own-stable-session-id>",
  "default_model": "opencode-default"
}
```

No session id? Don't use this adapter — use a real provider key.

---

## Security model

- **No secrets in the client.** The Lumen side holds only the bridge token.
- **Auth is server-side and never client-asserted.** Identity = `Authorization:
  Bearer <token>` → `auth.tokens` → user + tier. Anonymous traffic is
  opt-in (`allow_anon`) and rate-limited per source IP.
- **Rate limits are server-computed** per (tier, identity); a restart resets
  in-memory windows (documented limitation).
- **Hard request caps** (`limits.*`): body bytes, prompt chars, history turns,
  files count/size, docs chars, response `max_tokens` clamp. Oversized body →
  `413 payload_too_large`; everything else malformed → `400 invalid_request`.
- **Redaction.** Every log line, error message, and response is passed through
  `carbonx_bridge.redact` — `sk-…`, GitHub tokens, AWS keys, PEM keys,
  connection strings, `Authorization`, `x-opencode-session`, `KEY=value` forms,
  `api_key`/`token` JSON fields are replaced with `[REDACTED_SECRET]`. Request
  bodies and headers are never logged.
- **No arbitrary URL proxying.** The bridge only talks to configured provider
  base URLs; there is no generic fetch proxy.
- **Clean errors only** (never raw upstream text): `400 401 403 404 405 413
  429 500 502`, JSON `{"error":{"type","message","code"}}`,
  plus `Retry-After` on 429.
- **Deployment**: run locally / behind your own TLS terminator. No cloud
  deployment path is included by default.

---

## Request limits (config `limits`)

| Key | Default | Enforced as |
|---|---|---|
| `max_body_bytes` | 262144 | 413 |
| `max_prompt_chars` | 8000 | 400 |
| `max_history_turns` | 40 | 400 |
| `max_files` | 5 | 400 |
| `max_file_bytes` | 65536 (per file) | 400 |
| `max_docs_chars` | 40000 | 400 |
| `max_response_tokens` | 2048 | server-side clamp |

`files`/`docs` are validated (count/size/budget) but **not ingested** in v1 —
they pass through as context hints to the provider.

---

## Running this with the Lumen AI client

The companion Lumen scripts (`scripts/bridge.luau`, `scripts/carbonx.luau`,
`bridge_server.py`, `carbonx_console.py`) in the CarbonX client project talk
to the single-user bridge daemon. The flow:

1. `python bridge_server.py` (keys live in `config.json`, never in Lua).
2. In Lumen, run `scripts/bridge.luau` (self-contained; polls the bridge),
   then `scripts/carbonx.luau` — `CarbonX.boot()` prints
   `[carbonx] CarbonX vX ready — providers: …`.
3. Chat from Python: `python carbonx_console.py` (plain text, `/mode`,
   `/apply|/yes`, `/reject|/no`, `/stop`, `/status`, `/reset`, `/index`,
   `/copy`).

This **CarbonX API Bridge** is the multi-user, provider-abstraction REST
version of that same idea. A Lumen client can target it directly with a tiny
adapter: `Bridge.rpc → POST /v1/chat` and `/v1/stream` (SSE) with the bearer
token from `Lumen`'s secure storage — no provider keys ever reach the game
environment.

---

## Development

```bash
python -m unittest discover -s tests -v
```

Tests are fully offline: network adapters run against in-process fake HTTP
servers, and the server suite exercises the real HTTP stack. CI runs the suite
on Python 3.11–3.14 plus a secret-scan smoke check.

## License

MIT — see [LICENSE](LICENSE).