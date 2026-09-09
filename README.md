# CarbonX API Bridge

A **Bring-Your-Own-Key** (BYOK) HTTP API bridge for LLMs, **for Windows**.
Clients (Lumen scripts, editors, consoles) talk to one stable JSON API; the
bridge handles auth, rate limits, and provider routing for OpenAI, Anthropic,
OpenRouter, Cursor, local models, or any custom endpoint. Lumen externally
supports Windows only, so the whole stack — client and bridge — is built for
Windows.

- **Pure Python 3.10+ standard library** — zero dependencies, zero downloads.
- **Zero-setup start**: if `config.json` is missing, the bridge creates one with
  a random access token and starts on a built-in mock provider immediately.
- **Secure by default**: auth required, per-tier server-side rate limits, hard
  request caps, and secret redaction at every logging boundary.
- **Honest streaming**: `text/event-stream` for providers that support it;
  others return `400 streaming_unsupported` (never faked).

---

## Quick start

**Option A — no Python needed (recommended for everyone else):**

1. Download the latest `carbonx-bridge.exe` from the
   [Releases](../../releases) page and put it in any folder.
2. **Double-click `carbonx-bridge.exe`.**

**Option B — from source:**

1. Install Python 3.10+ from https://python.org (tick *"Add python.exe to PATH"*).
2. Download this project and extract it.
3. **Double-click `run.bat`.**

The bridge writes `config.json` if needed (next to the `.exe`, or in the
project folder when run from source), prints the access token, and starts on
the mock provider so it works immediately.

### Using a real model

1. Open the control panel: `http://localhost:8787/`.
2. Paste an API key into a provider and click *Save & apply* — no restart.
   (Or edit `config.json` directly:
   `"openai": { "type": "openai", "api_key": "sk-your-real-key", "default_model": "gpt-4o-mini" }`.)

The access token printed at first startup goes into the Lumen/bridge client
settings, not into `config.json`. For multiple users on one server, hand each
user their own token from `auth.tokens`; provider keys stay server-side.

---

## Control panel

The bridge serves a small control panel in your browser, so you never have to
edit `config.json` by hand:

1. Start the bridge (double-click `run.bat`) — it prints the panel address.
2. Open `http://localhost:8787/` (the panel is loopback-only, so it only
   listens on the machine running the bridge).
3. From the panel you can:
   - **Copy your access token** (or generate new tokens for each user, and
     **delete** stale ones — the last remaining token can't be deleted).
   - **Paste API keys** into providers, switch the default provider, change a
     base URL or model. Click *Save & apply* — changes take effect
     immediately, no restart.
   - **Chat like ChatGPT** — a multi-turn chat panel that streams from the
     default provider, remembers the conversation while the page is open
     (scroll back through it), and keeps a tidy plain-text look. New chat
     clears the history; *New chat* is always one click away. Provider-side
     errors are shown as toasts so you know exactly what failed.
   - **Assistant identity** — the system prompt sent to the model with every
     message. It tells the AI what the bridge is and who Lumen is, and to
     answer in clean plain text instead of raw markdown. Edit it, reset to the
     built-in default, or blank it out to disable.
   - **Add provider** — plug in your own models without ever touching JSON.
     Presets fill in Ollama (`http://localhost:11434/v1`), LM Studio
     (`http://localhost:1234/v1`), OpenRouter, OpenCode, OpenAI, Anthropic, or
     start *Manual* and point it at any endpoint. Each adds a card and appears
     in the default-provider dropdown immediately. Provider names are
     validated up front and a bad definition is rejected (with the on-disk
     config left untouched) instead of breaking the bridge.
   - Pick a **theme** — Midnight, Light, Ocean, Forest or Sunset, all with a
     frosted-glass look — the panel remembers your choice next time.
   - Every action (token create/delete, config save) pops a status toast, so
     nothing ever happens silently.
   - Keys are never shown again after saving; the panel only reports whether a
     key is set.

If the bridge prints `http://localhost:8787/`, just open it while the bridge
is running. There is no separate install — the panel is part of the bridge.

---

## Portable .exe (no Python)

`carbonx-bridge.exe` is a single ~9 MB file built with PyInstaller:

- **No Python, no downloads, nothing else to install.** Double-click and go.
- On first run it creates `config.json` **next to itself**, so you can keep
  the whole folder or zip it up and hand it to someone (they get *their own*
  random access token on first run).
- Built from source with `build.bat` (installs PyInstaller into a throwaway
  `.venv-build` folder). CI also builds it on every `v*` tag and attaches the
  `.exe` to the GitHub Release.
- Works the same as the source version: control panel, mock provider, `--port`,
  `--open`, everything above.

---

## Endpoints

| Endpoint | Auth | Description |
|---|---|---|
| `GET /health` | no | Liveness; version, providers, `require_auth`. |
| `GET /v1/models` | yes | `{"objects": [{"provider", "model", "supports_streaming", ...}]}` |
| `POST /v1/chat` | yes | Non-streaming response: `{"id","provider","model","content","usage","finish_reason"}` |
| `POST /v1/stream` | yes | `text/event-stream` (see below). |
| `POST /v1/game-context` | yes | Give the AI a live Lumen snapshot of the user's game (game name, players, decompiled files), attached to every chat. |
| `DELETE /v1/game-context` | yes | Forget the attached snapshot. |

`Authorization: Bearer <token>` maps to a configured user + tier. Client
supplied user/role/plan fields are ignored.

### Chat

```bash
curl http://localhost:8787/v1/chat \
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
curl -N http://localhost:8787/v1/stream \
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
`[DONE]`.

### Assistant identity (system prompt)

Every chat (and stream) request gets a system prompt that tells the AI what
the bridge is, who Lumen is, and to answer in clean plain text — no `**`, no
markdown tables. Configure it in `config.json`:

```json
"server": { "system_prompt": "Your custom identity text..." }
```

Leave the key present but empty to disable injection entirely. Precedence per
request: the **client's own** `system` (or a leading system-role message) wins
over the configured default, and the Lumen game snapshot (if any) is appended
after whichever prompt is used. The panel also exposes this under *Assistant
identity* with a reset-to-default button.

---

## Game context (Lumen)

The bridge has no live connection to Roblox, so the Lumen executor pushes a
snapshot of what the user is doing and the bridge attaches it to every chat.
With a snapshot attached the AI can answer *"what game am I in"*, *"list the
players"*, and read the decompiled game files *before* writing or explaining
anything about the game's code.

Push from a Lumen script (any recent universe/player data + decompiled scripts
you've read out — a handful of representative files is plenty):

```lua
local request = request -- Lumen's HTTP request (see getlumen.net/docs)

request({
  Url = "http://127.0.0.1:8787/v1/game-context",
  Method = "POST",
  Headers = {
    ["Content-Type"] = "application/json",
    Authorization = "Bearer " .. MY_ACCESS_TOKEN,
  },
  Body = __gameJSON, -- { "game": "...", "players": [ {...} ], "files": [ { "path": "...", "content": "..." } ] }
})
```

The snapshot lives **in memory only** — it is never written to disk — and is
dropped on restart. The panel shows a *"Game context attached"* pill while one
is loaded (with a *Forget game context* button). Limits:

| Key | Default | Behavior |
|---|---|---|
| `max_game_files` | 40 | more files → `413 payload_too_large` |
| `max_game_chars` | 60000 | total content budget; later files are truncated (and reported in the POST response as `truncated_files`) |

Send nothing to `/v1/chat` about the game and the model simply won't know it.
Clearing the context (DELETE, panel button, or restart) is enough to stop it
from being injected.

---

## Providers

| `type` | Wire format | Notes |
|---|---|---|
| `openai` | OpenAI Chat Completions | `base_url` adjustable (Ollama, vLLM, etc.) |
| `anthropic` | Anthropic Messages | `x-api-key` + `anthropic-version`; event/data SSE |
| `openrouter` | OpenAI-compatible | optional `HTTP-Referer` / `X-Title` via `headers` |
| `custom` / `other` | OpenAI or Anthropic | any endpoint via `base_url` (Cursor, local servers) |
| `opencode` | OpenAI-compatible | requires `x_opencode_session` (opt-in) |
| `mock` | none (in-process) | deterministic fake for tests and CI |

### Custom providers (`custom` / `other`)

For any endpoint with an OpenAI- or Anthropic-compatible API:

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

- `base_url` — required.
- `api_style` — `openai` (default) or `anthropic`.
- `api_key` — optional; sent as `Authorization: Bearer` (openai style) or
  `x-api-key` (anthropic style) when present.
- `headers` — optional extra headers on every call (`env:VAR` supported).
- Streaming, JSON mode, error mapping, and rate limiting behave like the named
  providers.

### OpenCode

OpenCode's free Zen Go tier requires an `x-opencode-session` header
(`MissingSessionID` otherwise). The `opencode` provider refuses to build unless
you supply your own session id:

```json
"opencode": {
  "type": "opencode",
  "api_key": "env:OPENROUTER_API_KEY",
  "x_opencode_session": "<existing-session-id>",
  "default_model": "opencode-default"
}
```

---

## Command line

```bash
python -m carbonx_bridge                 # run (auto-creates config.json if needed)
python -m carbonx_bridge --token <token> # add a token without editing config.json
python -m carbonx_bridge --port 9000     # change the port
python -m carbonx_bridge --host 0.0.0.0  # listen for other machines
python -m carbonx_bridge --open          # open the control panel in your browser
python -m carbonx_bridge --version
```

---

## Security

- **Auth is server-side.** Identity comes from `Authorization: Bearer <token>`
  → `auth.tokens` → user + tier. Anonymous traffic is opt-in (`allow_anon`) and
  rate-limited per source IP.
- **Rate limits** are computed per (tier, identity); in-memory windows reset on
  restart.
- **Hard request caps** (`limits.*`): body bytes, prompt chars, history turns,
  files count/size, docs chars, response `max_tokens` clamp. Oversized body →
  `413 payload_too_large`; other malformed requests → `400 invalid_request`.
- **Redaction.** Every log line, error message, and response passes through
  `carbonx_bridge.redact`: `sk-…`, GitHub tokens, AWS keys, PEM keys,
  connection strings, `Authorization`, `x-opencode-session`, `KEY=value` forms,
  and `api_key`/`token` JSON fields become `[REDACTED_SECRET]`. Request bodies
  and headers are never logged.
- **No arbitrary URL proxying.** The bridge only talks to configured provider
  base URLs.
- **Control panel is loopback-only and DNS-rebinding-proof.** `/` and the
  `/ui/*` endpoints only answer when the request (a) comes from the loopback
  interface, (b) has a `Host` header that names a loopback host (`127.0.0.1`,
  `localhost`, `::1`), and (c) isn't flagged as cross-site by the browser
  (`Sec-Fetch-Site`, `Origin`). This blocks the classic attacks against an
  unauthenticated local panel: a malicious website **can't** read or click it
  via CSRF, and a rebinding domain that resolves to `127.0.0.1` gets a 403.
  Locally installed browser add-ons (AI assistants like Merlin) are tolerated —
  their `chrome-extension://…`-style `Origin` can only be produced by an
  installed extension, never by a remote page — so they keep working without
  re-opening the surface to the internet. Keys written through the panel are
  never returned or logged. The `/v1/*` API
  intentionally stays open to non-loopback clients (LAN Lumen instances) — so
  only the *admin* surface is locked down, and `--host 0.0.0.0` remains safe
  for sharing the API, not the panel.
- **Is plain HTTP safe?** The panel is bound to `127.0.0.1` by default, so its
  traffic never crosses the network — HTTP on loopback is fine, and that's the
  default posture. Treat remote exposure like any service on your LAN: don't
  port-forward, don't put it on the internet without a TLS terminator in front,
  and don't expose the panel. (Local malware that already has code execution
  on the machine is out of scope for any local server.)
- **Clean errors only**: `400 401 403 404 405 413 429 500 502`, JSON
  `{"error":{"type","message","code"}}`, plus `Retry-After` on 429.
- **Deployment**: run locally or behind your own TLS terminator.

---

## Request limits

| Key | Default | Enforced as |
|---|---|---|
| `max_body_bytes` | 262144 | 413 |
| `max_prompt_chars` | 8000 | 400 |
| `max_history_turns` | 40 | 400 |
| `max_files` | 5 | 400 |
| `max_file_bytes` | 65536 (per file) | 400 |
| `max_docs_chars` | 40000 | 400 |
| `max_response_tokens` | 2048 | server-side clamp |
| `max_game_files` | 40 | game context: 413 |
| `max_game_chars` | 60000 | game context: truncate |

`files`/`docs` are validated but not ingested in v1 — they pass through as
context hints to the provider.

---

## Lumen integration

The companion Lumen scripts (`scripts/bridge.luau`, `scripts/carbonx.luau`,
`bridge_server.py`, `carbonx_console.py`) in the CarbonX client project talk to
the single-user bridge daemon:

1. `python bridge_server.py` (keys live in `config.json`, never in Lua).
2. In Lumen, run `scripts/bridge.luau`, then `scripts/carbonx.luau` —
   `CarbonX.boot()` prints `[carbonx] CarbonX vX ready — providers: …`.
3. Chat from Python: `python carbonx_console.py` (`/mode`, `/apply|/yes`,
   `/reject|/no`, `/stop`, `/status`, `/reset`, `/index`, `/copy`).

A Lumen client can target this API bridge directly via `POST /v1/chat` and
`/v1/stream` (SSE) with the bearer token from secure storage; provider keys
never reach the client.

---

## Development

```bash
python -m unittest discover -s tests -v
```

Tests are fully offline: adapters run against in-process fake HTTP servers, and
the server suite exercises the real HTTP stack. CI runs on Python 3.11–3.14
plus a secret-scan smoke check.

## License

MIT — see [LICENSE](LICENSE).