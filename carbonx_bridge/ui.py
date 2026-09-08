"""Local control-panel page + helpers.

The bridge serves a small, single-file HTML panel at ``/`` so Windows users
can manage the bridge without editing JSON. It is stdlib-only (no CDN, no
frameworks) and shows/edits nothing that leaves the machine:

*  GET /                 -> this page (no secrets in the markup)
*  GET /ui/status        -> same shape as /health
*  GET /ui/token         -> all configured tokens           (loopback only)
*  POST /ui/token        -> generate + persist a new token  (loopback only)
*  GET /ui/config        -> sanitized config view           (loopback only;
                             api_key values are NEVER returned)
*  POST /ui/config       -> apply provider/token edits      (loopback only)

All ``/ui/*`` endpoints are gated on the client being on-loopback, so the
panel only ever exposes secrets to a browser on the machine running the
bridge (the same trust model as the default ``127.0.0.1`` bind).
"""

from __future__ import annotations


def is_loopback(ip):
    return ip in ("127.0.0.1", "::1", "localhost")


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CarbonX API Bridge</title>
<style>
  :root { color-scheme: light dark; --b:#1f2937; --b2:#111827; --fg:#f9fafb; --m:#9ca3af; --a:#34d399; --r:#7c3aed; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: "Segoe UI", system-ui, sans-serif; background:var(--b2); color:var(--fg); }
  header { padding: 22px 28px; border-bottom:1px solid #374151; display:flex; align-items:center; gap:14px; flex-wrap:wrap; }
  header h1 { font-size:20px; margin:0; }
  header .dot { width:10px; height:10px; border-radius:50%; background:#f87171; display:inline-block; }
  header .dot.ok { background:var(--a); }
  main { max-width:860px; margin:0 auto; padding:24px 20px 60px; }
  .card { background:var(--b); border:1px solid #374151; border-radius:12px; padding:18px 20px; margin-bottom:18px; }
  .card h2 { font-size:15px; margin:0 0 12px; text-transform:uppercase; letter-spacing:.5px; color:var(--m); }
  .mono { font-family: Consolas, "Courier New", monospace; word-break: break-all; }
  .kv { display:grid; grid-template-columns: 180px 1fr; gap:6px 12px; font-size:14px; }
  .kv b { color:var(--m); font-weight:600; }
  button { background:var(--r); color:#fff; border:0; border-radius:8px; padding:9px 14px; font-size:14px; cursor:pointer; }
  button:hover { filter:brightness(1.1); }
  button.ghost { background:#374151; }
  .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  input, select { background:#111827; color:var(--fg); border:1px solid #4b5563; border-radius:8px; padding:9px 10px; font-size:14px; }
  input[type=text], input[type=password] { flex:1; min-width:200px; font-family: Consolas, monospace; }
  .prov { border:1px solid #4b5563; border-radius:10px; padding:12px 14px; margin-bottom:10px; background:#111827; }
  .prov .name { font-weight:700; }
  .prov .hint { color:var(--m); font-size:12px; }
  .prov .fields { display:grid; gap:8px; margin-top:10px; }
  .prov .fields .lbl { font-size:12px; color:var(--m); margin-bottom:2px; }
  .ok, .err { display:none; padding:10px 12px; border-radius:8px; margin-bottom:14px; font-size:14px; }
  .ok { background:#064e3b; color:#6ee7b7; }
  .err { background:#7f1d1d; color:#fca5a5; }
  .tok { display:flex; gap:8px; align-items:center; margin-bottom:8px; }
  .tok code { flex:1; background:#111827; border:1px solid #4b5563; padding:8px 10px; border-radius:8px; }
  .muted { color:var(--m); font-size:13px; }
</style>
</head>
<body>
<header>
  <h1>CarbonX API Bridge <span class="dot" id="dot"></span></h1>
  <div class="muted" id="statusline">…</div>
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
</main>
<script>
const $ = (id) => document.getElementById(id);
let CONFIG = { providers: {}, default_provider: "" };

function flash(el, msg) { el.textContent = msg; el.style.display = "block"; setTimeout(() => el.style.display = "none", 6000); }
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
    $("statusline").textContent = "v" + h.version + " · " + h.providers.join(", ") + " · auth " + (h.require_auth ? "on" : "OFF");
  } catch (e) { $("dot").className = "dot"; $("statusline").textContent = "offline"; }
}

function copy(el) {
  const code = el.previousElementSibling;
  const sel = window.getSelection(), range = document.createRange();
  range.selectNodeContents(code); sel.removeAllRanges(); sel.addRange(range);
  try { document.execCommand("copy"); flash($("ok"), "Copied."); } catch (e) { flash($("err"), "Select the token and copy it manually."); }
}

async function loadTokens() {
  try {
    const d = await api("GET", "/ui/token");
    $("tokens").innerHTML = d.tokens.map((t) =>
      '<div class="tok"><code>' + t.token + '</code><button class="ghost" onclick="copy(this)">Copy</button></div>' +
      '<div class="muted" style="margin:-4px 0 10px">user: ' + t.user + ' · tier: ' + t.tier + '</div>'
    ).join("") || '<div class="muted">No tokens configured.</div>';
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
    ? '<div class="lbl">API style</div><select data-f="api_style"><option value="openai"' + (p.api_style === "openai" ? " selected" : "") + '>openai</option><option value="anthropic"' + (p.api_style === "anthropic" ? " selected" : "") + '>anthropic</option></select>'
    : "";
  const keyNote = p.has_key ? "" : '<span class="hint"> no key yet</span>';
  return '<div class="prov">' +
    '<div class="row"><span class="name">' + name + '</span><span class="hint">' + p.type + keyNote + '</span></div>' +
    '<div class="fields">' +
      '<div><div class="lbl">API key (blank keeps current, ' + (p.has_key ? "already set" : "or removes none") + ')</div><input type="password" data-f="api_key" placeholder="' + (p.has_key ? "key is set — leave blank to keep" : "paste key here") + '"></div>' +
      '<div><div class="lbl">Base URL (custom providers only; leave blank to keep)</div><input type="text" data-f="base_url" value="' + (p.base_url || "") + '"></div>' +
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
    flash($("ok"), "Saved and applied. " + (d.notes && d.notes.length ? d.notes.join(" ") : ""));
    await loadConfig(); await loadTokens(); await refreshStatus();
  } catch (e) { flash($("err"), e.message); }
}
$("save").addEventListener("click", saveConfig);

refreshStatus(); loadTokens(); loadConfig();
</script>
</body>
</html>
"""