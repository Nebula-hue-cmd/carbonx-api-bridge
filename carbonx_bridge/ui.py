"""Local control-panel page + helpers.

The bridge serves a small, single-file HTML panel at ``/`` so Windows users
can manage the bridge without editing JSON. It is stdlib-only (no CDN, no
frameworks) and shows/edits nothing that leaves the machine:

*  GET  /                 -> this page (no secrets in the markup)
*  GET  /ui/status        -> same shape as /health
*  GET  /ui/token         -> all configured tokens              (loopback only)
*  POST /ui/token         -> generate + persist a new token     (loopback only)
*  POST /ui/token {action:"delete", token: t} -> remove a token
                             (the last token cannot be deleted) (loopback only)
*  GET  /ui/config        -> sanitized config view              (loopback only;
                             api_key values are NEVER returned)
*  POST /ui/config        -> apply provider/token edits         (loopback only)

All ``/ui/*`` endpoints are gated on the client being on-loopback, so the
panel only ever exposes secrets to a browser on the machine running the
bridge (the same trust model as the default ``127.0.0.1`` bind).
"""

from __future__ import annotations


def is_loopback(ip):
    return ip in ("127.0.0.1", "::1", "localhost")


PAGE = """<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CarbonX API Bridge</title>
<script>try{var q=(location.search.match(/theme=([a-z]+)/)||[])[1];var t=q||localStorage.getItem("carbonx-theme")||"dark";document.documentElement.setAttribute("data-theme",t);}catch(e){}</script>
<style>
  :root {
    /* Midnight (default) */
    color-scheme: dark;
    --bg: #111827; --card: #1f2937; --line: #374151; --fg: #f9fafb; --m: #9ca3af;
    --acc: #7c3aed; --acc-fg: #ffffff; --ghost: #374151; --ghost-fg: #f9fafb;
    --input: #111827; --input-line: #4b5563;
    --okb: #064e3b; --okf: #6ee7b7; --errb: #7f1d1d; --errf: #fca5a5;
    --dot: #f87171; --prov-bg: #111827;
  }
  [data-theme="light"] {
    color-scheme: light;
    --bg: #f3f4f6; --card: #ffffff; --line: #e5e7eb; --fg: #111827; --m: #6b7280;
    --acc: #6d28d9; --acc-fg: #ffffff; --ghost: #eef0f3; --ghost-fg: #374151;
    --input: #ffffff; --input-line: #d1d5db;
    --okb: #d1fae5; --okf: #065f46; --errb: #fee2e2; --errf: #b91c1c;
    --dot: #f87171; --prov-bg: #ffffff;
  }
  [data-theme="ocean"] {
    color-scheme: dark;
    --bg: #07171f; --card: #0e2733; --line: #1e4257; --fg: #e2f3fb; --m: #8fb7c9;
    --acc: #2dd4bf; --acc-fg: #042f2e; --ghost: #1e4257; --ghost-fg: #e2f3fb;
    --input: #0a1d26; --input-line: #2a5a70;
    --okb: #083344; --okf: #5eead4; --errb: #451a26; --errf: #fda4af;
    --dot: #f87171; --prov-bg: #0a1d26;
  }
  [data-theme="forest"] {
    color-scheme: dark;
    --bg: #0d1a10; --card: #16251a; --line: #2a4230; --fg: #e6f4e8; --m: #8fae96;
    --acc: #a3e635; --acc-fg: #1a2e05; --ghost: #2a4230; --ghost-fg: #e6f4e8;
    --input: #101c14; --input-line: #3a5a42;
    --okb: #14532d; --okf: #86efac; --errb: #450a0a; --errf: #fecaca;
    --dot: #f87171; --prov-bg: #101c14;
  }
  [data-theme="sunset"] {
    color-scheme: dark;
    --bg: #1a0f12; --card: #2a1a20; --line: #4a2a35; --fg: #fbe9e7; --m: #c09a92;
    --acc: #fb923c; --acc-fg: #3b1206; --ghost: #4a2a35; --ghost-fg: #fbe9e7;
    --input: #1d0f13; --input-line: #5a3340;
    --okb: #38260d; --okf: #fcd34d; --errb: #4c1d1d; --errf: #fda4af;
    --dot: #f87171; --prov-bg: #1d0f13;
  }
  * { box-sizing: border-box; }
  body { margin:0; font-family: "Segoe UI", system-ui, sans-serif; background:var(--bg); color:var(--fg); }
  header { padding: 22px 28px; border-bottom:1px solid var(--line); display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
  header h1 { font-size:20px; margin:0; }
  header .dot { width:10px; height:10px; border-radius:50%; background:var(--dot); display:inline-block; }
  header .dot.ok { background:var(--okf); }
  header .right { margin-left:auto; display:flex; align-items:center; gap:10px; }
  main { max-width:860px; margin:0 auto; padding:24px 20px 60px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:18px 20px; margin-bottom:18px; }
  .card h2 { font-size:15px; margin:0 0 12px; text-transform:uppercase; letter-spacing:.5px; color:var(--m); }
  .mono { font-family: Consolas, "Courier New", monospace; word-break: break-all; }
  .kv { display:grid; grid-template-columns: 180px 1fr; gap:6px 12px; font-size:14px; }
  .kv b { color:var(--m); font-weight:600; }
  button { background:var(--acc); color:var(--acc-fg); border:0; border-radius:8px; padding:9px 14px; font-size:14px; cursor:pointer; font-weight:600; }
  button:hover { filter:brightness(1.12); }
  button:disabled { opacity:.45; cursor:wait; }
  button.ghost { background:var(--ghost); color:var(--ghost-fg); }
  button.danger { background:transparent; color:var(--errf); border:1px solid var(--errb); font-weight:600; }
  .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  input, select, textarea { background:var(--input); color:var(--fg); border:1px solid var(--input-line); border-radius:8px; padding:9px 10px; font-size:14px; }
  input[type=text], input[type=password] { flex:1; min-width:200px; font-family: Consolas, monospace; }
  textarea { width:100%; font-family: "Segoe UI", system-ui, sans-serif; resize:vertical; }
  select { cursor:pointer; min-width:110px; }
  .prov { border:1px solid var(--input-line); border-radius:10px; padding:12px 14px; margin-bottom:10px; background:var(--prov-bg); }
  .prov .name { font-weight:700; }
  .prov .hint { color:var(--m); font-size:12px; }
  .prov .fields { display:grid; gap:8px; margin-top:10px; }
  .prov .fields .lbl { font-size:12px; color:var(--m); margin-bottom:2px; }
  .ok, .err { display:none; padding:10px 12px; border-radius:8px; margin-bottom:14px; font-size:14px; }
  .ok { background:var(--okb); color:var(--okf); }
  .err { background:var(--errb); color:var(--errf); }
  .tok { display:flex; gap:8px; align-items:center; margin-bottom:6px; }
  .tok code { flex:1; background:var(--prov-bg); border:1px solid var(--input-line); padding:8px 10px; border-radius:8px; font-family: Consolas, "Courier New", monospace; word-break: break-all; }
  .tok .meta { margin:-2px 0 10px 2px; }
  .out { background:var(--prov-bg); border:1px solid var(--input-line); border-radius:8px; padding:12px; font-family: Consolas, "Courier New", monospace; white-space: pre-wrap; word-break: break-word; min-height:52px; margin-top:10px; }
  .out.errbox { background:var(--errb); color:var(--errf); border-color:var(--errb); }
  .muted { color:var(--m); font-size:13px; }
</style>
</head>
<body>
<header>
  <h1>CarbonX API Bridge <span class="dot" id="dot"></span></h1>
  <div class="muted" id="statusline">&hellip;</div>
  <div class="right">
    <label class="muted" for="theme">Theme</label>
    <select id="theme">
      <option value="dark">Midnight</option>
      <option value="light">Light</option>
      <option value="ocean">Ocean</option>
      <option value="forest">Forest</option>
      <option value="sunset">Sunset</option>
    </select>
  </div>
</header>
<main>
  <div class="ok" id="ok"></div>
  <div class="err" id="err"></div>

  <div class="card">
    <h2>Access token (for Lumen / bridge settings)</h2>
    <div id="tokens"></div>
    <div class="row" style="margin-top:8px">
      <button id="newtok" class="ghost">Generate new token</button>
      <span class="muted">Multi-user? Give each person their own token.</span>
    </div>
  </div>

  <div class="card">
    <h2>Models</h2>
    <div class="row">
      <label class="muted" for="default">Default provider</label>
      <select id="default"></select>
      <button id="save">Save &amp; apply</button>
    </div>
    <div class="muted" style="margin-top:8px">Paste an API key to add or replace a model's key. Leave a key blank to keep the current one. No config.json editing, no restart.</div>
    <div id="provs" style="margin-top:12px"></div>
  </div>

  <div class="card">
    <h2>Try it now</h2>
    <textarea id="try" rows="3" placeholder="Ask the bridge something&hellip; (uses the default provider)"></textarea>
    <div class="row" style="margin-top:8px">
      <button id="trysend">Send</button>
      <span class="muted" id="trystate"></span>
    </div>
    <pre class="out" id="triesult"></pre>
  </div>
</main>
<script>
const $ = (id) => document.getElementById(id);
let CONFIG = { providers: {}, default_provider: "" };

function flash(el, msg) { el.textContent = msg; el.style.display = "block"; setTimeout(() => el.style.display = "none", 6500); }
async function api(method, path, body) {
  const r = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((data.error && data.error.message) || ("HTTP " + r.status));
  return data;
}

async function refreshStatus() {
  try {
    const h = await api("GET", "/health");
    $("dot").className = "dot ok";
    $("statusline").textContent = "v" + h.version + " \u00b7 " + h.providers.join(", ") + " \u00b7 auth " + (h.require_auth ? "on" : "\u26a0 OFF");
  } catch (e) { $("dot").className = "dot"; $("statusline").textContent = "offline"; }
}

function copy(el) {
  const code = el.previousElementSibling;
  const sel = window.getSelection(), range = document.createRange();
  range.selectNodeContents(code); sel.removeAllRanges(); sel.addRange(range);
  try { document.execCommand("copy"); flash($("ok"), "Copied."); } catch (e) { flash($("err"), "Select the token and copy it manually."); }
}

async function delToken(tok) {
  try {
    await api("POST", "/ui/token", { action: "delete", token: tok });
    flash($("ok"), "Token deleted.");
    await loadTokens(); await refreshStatus();
  } catch (e) { flash($("err"), e.message); }
}

function tokenRow(t) {
  return '<div class="tok"><code>' + t.token + '</code>' +
    '<button class="ghost" onclick="copy(this)">Copy</button>' +
    '<button class="danger" onclick="delToken(\'' + t.token + '\')">Delete</button></div>' +
    '<div class="muted meta">user: ' + t.user + ' \u00b7 tier: ' + t.tier + '</div>';
}

async function loadTokens() {
  try {
    const d = await api("GET", "/ui/token");
    $("tokens").innerHTML = (d.tokens && d.tokens.length)
      ? d.tokens.map(tokenRow).join("")
      : '<div class="muted">No tokens configured.</div>';
  } catch (e) { flash($("err"), "Could not load tokens: " + e.message); }
}

async function loadConfig() {
  try {
    CONFIG = await api("GET", "/ui/config");
    const sel = $("default");
    sel.innerHTML = Object.keys(CONFIG.providers).map((n) => '<option value="' + n + '"' + (n === CONFIG.default_provider ? " selected" : "") + ">" + n + "</option>").join("");
    $("provs").innerHTML = Object.keys(CONFIG.providers).map(buildProviderCard).join("");
  } catch (e) { flash($("err"), "Could not load config: " + e.message); }
}

function buildProviderCard(name) {
  const p = CONFIG.providers[name];
  const styleField = (p.type === "custom" || p.type === "other")
    ? '<div><div class="lbl">API style</div><select data-f="api_style"><option value="openai"' + (p.api_style === "openai" ? " selected" : "") + '>openai</option><option value="anthropic"' + (p.api_style === "anthropic" ? " selected" : "") + '>anthropic</option></select></div>'
    : "";
  const keyNote = p.has_key ? ' <span class="hint">key set</span>' : ' <span class="hint">no key yet</span>';
  return '<div class="prov">' +
    '<div class="row"><span class="name">' + name + '</span><span class="hint">' + p.type + keyNote + '</span></div>' +
    '<div class="fields">' +
      '<div><div class="lbl">API key (blank keeps the current key)</div><input type="password" data-f="api_key" placeholder="' + (p.has_key ? "key already set \u2014 leave blank to keep" : "paste key here") + '"></div>' +
      '<div><div class="lbl">Base URL (blank keeps current)</div><input type="text" data-f="base_url" value="' + (p.base_url || "") + '"></div>' +
      '<div><div class="lbl">Default model</div><input type="text" data-f="default_model" value="' + (p.default_model || "") + '"></div>' +
      styleField +
    '</div></div>';
}

async function genToken() {
  try {
    const d = await api("POST", "/ui/token", {});
    flash($("ok"), "New token created: " + d.token);
    await loadTokens(); await refreshStatus();
  } catch (e) { flash($("err"), e.message); }
}
$("newtok").addEventListener("click", genToken);

async function saveConfig() {
  try {
    const patch = { default_provider: $("default").value, providers: {} };
    for (const card of $("provs").children) {
      const name = card.querySelector(".name").textContent;
      const p = {};
      for (const el of card.querySelectorAll("[data-f]")) {
        if (el.dataset.f === "api_key") { if (el.value.trim()) p.api_key = el.value.trim(); }
        else if (el.value.trim()) p[el.dataset.f] = el.value.trim();
      }
      if (Object.keys(p).length) patch.providers[name] = p;
    }
    const d = await api("POST", "/ui/config", patch);
    flash($("ok"), "Saved and applied. " + ((d.note) ? d.note : ""));
    await loadConfig(); await loadTokens(); await refreshStatus();
  } catch (e) { flash($("err"), e.message); }
}
$("save").addEventListener("click", saveConfig);

async function tryChat() {
  const text = $("try").value.trim();
  if (!text) { flash($("err"), "Type a message first."); return; }
  $("trysend").disabled = true; $("trystate").textContent = "thinking\u2026";
  try {
    const t = await api("GET", "/ui/token");
    const tok = (t.tokens && t.tokens[0]) ? t.tokens[0].token : null;
    if (!tok) throw new Error("No access token configured on this machine.");
    const r = await fetch("/v1/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + tok },
      body: JSON.stringify({ messages: [{ role: "user", content: text }], model: CONFIG.default_provider })
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((data.error && data.error.message) || ("HTTP " + r.status));
    $("triesult").className = "out";
    $("triesult").textContent = data.content || JSON.stringify(data, null, 2);
  } catch (e) {
    $("triesult").className = "out errbox";
    $("triesult").textContent = "Error: " + e.message;
  } finally {
    $("trysend").disabled = false; $("trystate").textContent = "";
  }
}
$("trysend").addEventListener("click", tryChat);
$("try").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) tryChat(); });

// theme
const themeSel = $("theme");
themeSel.value = document.documentElement.getAttribute("data-theme") || "dark";
themeSel.addEventListener("change", () => {
  const t = themeSel.value;
  document.documentElement.setAttribute("data-theme", t);
  try { localStorage.setItem("carbonx-theme", t); } catch (e) {}
});

refreshStatus(); loadTokens(); loadConfig();
</script>
</body>
</html>
"""