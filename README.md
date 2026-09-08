# CarbonX API Bridge

A **Bring-Your-Own-Key** (BYOK), provider-abstraction HTTP API bridge for LLMs.
Your Lumen scripts, editors, and consoles talk to one stable, minimal JSON API;
the bridge talks to OpenAI, Anthropic, OpenRouter, Cursor, local models
(Ollama/vLLM/etc.), or anything else you can point it at — on a per-user,
server-side-managed basis.

- **Pure Python 3.10+ standard library** — zero dependencies, zero downloads.
- **Zero-setup start**: no `config.json`? The bridge creates one for you with a
  random access token and starts on a built-in mock AI immediately.
- **Secure by default**: auth required, per-tier server-side rate limits,
  hard request caps, and secret redaction at every logging boundary.
- **Honest streaming**: `text/event-stream` for providers that support it;
  providers that cannot stream return `400 streaming_unsupported` (never faked).

```
Lumen / Luau client ── HTTPS/JSON (bearer token) ──> CarbonX API Bridge ──> Provider (BYOK) ──> LLM
```

---

## Setup in 30 seconds (Windows)

1. **Install Python** from https://python.org (tick *"Add python.exe to PATH"*).
2. **Download this project** (GitHub → green *Code* button → Download ZIP →
   extract it anywhere).
3. **Double-click `run.bat`**.

That's it. The bridge writes `config.json` for you, prints your access token,
and starts running with the mock AI so you can verify it works.

To use a **real** model:

1. Open `config.json` (right-click → Open with → Notepad).
2. Find one of the provider blocks, e.g.:
   ```json
   "openai": { "type": "openai", "api_key": "", "default_model": "gpt-4o-mini" }
   ```
3. Paste your key between the quotes: `"api_key": "sk-your-real-key"`.
4. Save, close the window, double-click `run.bat` again.

Your access token (printed the first time) goes **into the Lumen/bridge
settings**, never into `config.json` on machines you don't fully control —
and never into any shared file.

> **Multiple users on one server?** Put each person's `api_key` in your
> server's `config.json`, and hand out random tokens from `auth.tokens` (one
> per person). The bridge keeps all keys server-side; users only ever hold
> their token.

---

## What is `127.0.0.1:8787`, and will it break for other people?

**No — it works for everyone, as long as they run everything on one computer.**

`127.0.0.1` (or "localhost") is not *your* address. It is a name every single
computer uses for **itself**. When the bridge prints
`listening on http://127.0.0.1:8787`, it means *"listening on this computer,
port 8787"* — on any machine, with no changes.

- **Bridge + Lumen on the same PC** → `127.0.0.1:8787` works with zero edits,
  everywhere.
- **Bridge on a different machine** (e.g. a shared home server) → set
  `server.host` to `0.0.0.0` in `config.json`, and in the Lumen client set the
  URL to `http://<that-server's-ip>:8787`. `127.0.0.1` still just means
  "this machine" — you must give the *other* machine's address.
- **Ports** are changeable too: `server.port`, or `--port 9000` on the command
  line.

The URL is configurable on **both** sides — there is no hardcoded personal
address anywhere.

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
| `custom` / `other` | OpenAI **or** Anthropic | **bring your own** — Cursor, any local server |
| `opencode` | OpenAI-compatible | explicit opt-in — requires `x_opencode_session` |
| `mock` | none (in-process) | deterministic fake for local testing, CI |

### Bring your own model (`custom` / `other`)

For anything not in the list — Cursor's local proxy, vLLM, LiteLLM, llama.cpp,
LM Studio, Open WebUI, a teammate's GPU box, a new service that launched last
week. Pick the wire format it speaks; most things speak OpenAI's, Cursor
speaks both:

```json
"cursor": {
  "type": "custom",
  "api_style": "anthropic",
  "base_url": "http://localhost:3000",
  "api_key": "",
  "default_model": "cursor-fast"
},
"local-llm": {
  "type": "custom",
  "api_style": "openai",
  "base_url": "http://localhost:8080/v1",
  "default_model": "my-model"
}
```

- `base_url` — **required** (no default: it points at *your* endpoint).
- `api_style` — `openai` (default) or `anthropic`; Cursor exposes both.
- `api_key` — optional; sent as `Authorization: Bearer` (openai style) or
  `x-api-key` (anthropic style) only when present.
- `headers` — optional extra headers merged on every call (`env:VAR`
  supported), e.g. API keys some local servers expect.
- Streaming, JSON mode, error mapping, and rate limiting behave exactly like
  the named providers.

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

## Command-line options

While `run.bat` / `python -m carbonx_bridge` needs nothing, there are extras:

```bash
python -m carbonx_bridge                 # run (auto-creates config.json if needed)
python -m carbonx_bridge --token mytoken # add a token without editing config.json
python -m carbonx_bridge --port 9000     # different port
python -m carbonx_bridge --host 0.0.0.0  # listen for other machines
python -m carbonx_bridge --version
```

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