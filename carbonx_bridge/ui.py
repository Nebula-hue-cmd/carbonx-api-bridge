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

All ``/ui/*`` endpoints are gated on the client being on-loopback, and the
whole admin surface (including ``/``) additionally rejects bad ``Host``
headers (DNS-rebinding) and cross-site requests (CSRF).
"""

from __future__ import annotations


def is_loopback(ip):
    return ip in ("127.0.0.1", "::1", "localhost")


def host_is_loopback(host_header):
    """True if a Host header names a loopback interface (rebinding guard)."""
    h = (host_header or "").strip()
    if h.startswith("["):  # [::1]:8787
        h = h[1 : h.find("]")]
    else:
        h = h.rsplit(":", 1)[0]  # strip :port
    return h.lower() in ("127.0.0.1", "localhost", "::1")


PAGE = """<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CarbonX API Bridge</title>
<script>try{var q=(location.search.match(/theme=([a-z]+)/)||[])[1];var t=q||localStorage.getItem("carbonx-theme")||"dark";document.documentElement.setAttribute("data-theme",t);}catch(e){}</script>
<style>
  :root {
    color-scheme: dark;
    --fg:#e7ecf5; --muted:#9aa7c0;
    --acc-from:#6366f1; --acc-to:#22d3ee; --acc-fg:#0b1020;
    --glass: rgba(148,163,184,.08); --glass-edge: rgba(255,255,255,.14);
    --surface: rgba(17,25,45,.62); --chip: rgba(8,13,28,.55); --chip-line: rgba(148,163,184,.20);
    --ok:#34d399; --err:#fb7185; --blob1:#4f46e5; --blob2:#9333ea; --blob3:#06b6d4;
    --shadow: 0 18px 48px rgba(2,6,23,.55), inset 0 1px 0 rgba(255,255,255,.09);
  }
  [data-theme="light"] { color-scheme: light;
    --fg:#1e293b; --muted:#5b6b82;
    --acc-from:#4f46e5; --acc-to:#0ea5e9; --acc-fg:#ffffff;
    --glass: rgba(255,255,255,.55); --glass-edge: rgba(255,255,255,.85);
    --surface: rgba(255,255,255,.72); --chip: rgba(255,255,255,.9); --chip-line: rgba(15,23,42,.12);
    --ok:#059669; --err:#e11d48; --blob1:#a5b4fc; --blob2:#f0abfc; --blob3:#7dd3fc;
    --shadow: 0 18px 44px rgba(30,41,59,.12), inset 0 1px 0 rgba(255,255,255,.9);
  }
  [data-theme="ocean"] { color-scheme: dark;
    --fg:#e8f6ff; --muted:#8fb7c9;
    --acc-from:#2dd4bf; --acc-to:#38bdf8; --acc-fg:#03141c;
    --glass: rgba(56,189,248,.07); --glass-edge: rgba(125,211,252,.22);
    --surface: rgba(7,32,44,.62); --chip: rgba(3,18,26,.55); --chip-line: rgba(125,211,252,.20);
    --ok:#5eead4; --err:#fb7185; --blob1:#0891b2; --blob2:#2563eb; --blob3:#0e7490;
    --shadow: 0 18px 48px rgba(1,15,23,.6), inset 0 1px 0 rgba(125,211,252,.15);
  }
  [data-theme="forest"] { color-scheme: dark;
    --fg:#e7f4e9; --muted:#93ab99;
    --acc-from:#a3e635; --acc-to:#34d399; --acc-fg:#0c1708;
    --glass: rgba(163,230,53,.06); --glass-edge: rgba(190,242,100,.22);
    --surface: rgba(10,26,16,.62); --chip: rgba(4,16,10,.55); --chip-line: rgba(190,242,100,.18);
    --ok:#86efac; --err:#fda4af; --blob1:#16a34a; --blob2:#65a30d; --blob3:#0d9488;
    --shadow: 0 18px 48px rgba(2,10,4,.55), inset 0 1px 0 rgba(190,242,100,.14);
  }
  [data-theme="sunset"] { color-scheme: dark;
    --fg:#fdefe7; --muted:#c9a195;
    --acc-from:#fb923c; --acc-to:#f472b6; --acc-fg:#2a0a00;
    --glass: rgba(251,146,60,.06); --glass-edge: rgba(254,205,211,.20);
    --surface: rgba(38,16,22,.62); --chip: rgba(24,8,12,.55); --chip-line: rgba(254,205,211,.18);
    --ok:#fcd34d; --err:#fda4af; --blob1:#ea580c; --blob2:#db2777; --blob3:#fb7185;
    --shadow: 0 18px 48px rgba(20,4,8,.55), inset 0 1px 0 rgba(254,205,211,.14);
  }
  @supports not ((backdrop-filter: blur(1px))) {
    :root { --glass: rgba(15,23,42,.94); --surface: rgba(10,16,32,.95); --chip: rgba(6,11,24,.96); }
    [data-theme="light"] { --glass: rgba(255,255,255,.98); --surface: rgba(255,255,255,.98); --chip: rgba(255,255,255,.99); }
  }
  @media (prefers-reduced-transparency: reduce) {
    :root { --glass: rgba(15,23,42,.9); --surface: rgba(10,16,32,.92); --chip: rgba(6,11,24,.94); }
    [data-theme="light"] { --glass: rgba(255,255,255,.97); --surface: rgba(255,255,255,.97); --chip: rgba(255,255,255,.99); }
  }
  * { box-sizing: border-box; }
  html { min-height: 100%; }
  body { margin:0; min-height:100vh; font-family:"Segoe UI Variable Text","Segoe UI",system-ui,sans-serif; color:var(--fg); background:#05080f; -webkit-font-smoothing:antialiased; }
  [data-theme="light"] body { background:#dce4f0; }
  body::before { content:""; position:fixed; inset:0; z-index:-2;
    background:
      radial-gradient(900px 600px at 12% -8%, var(--blob1), transparent 60%),
      radial-gradient(800px 640px at 88% 4%, var(--blob2), transparent 62%),
      radial-gradient(760px 560px at 55% 122%, var(--blob3), transparent 60%);
    background-attachment: fixed; }
  .blob { position:fixed; z-index:-1; border-radius:50%; filter:blur(80px); opacity:.55;
    animation:drift 26s ease-in-out infinite alternate; will-change:transform; }
  .blob.b1 { width:520px; height:520px; left:-140px; top:-120px; background:var(--blob1); }
  .blob.b2 { width:420px; height:420px; right:-120px; top:18%; background:var(--blob2); animation-delay:-9s; }
  .blob.b3 { width:520px; height:520px; left:30%; bottom:-220px; background:var(--blob3); animation-delay:-18s; }
  @keyframes drift { from { transform:translate3d(0,0,0) scale(1); } to { transform:translate3d(60px,40px,0) scale(1.12); } }
  @media (prefers-reduced-motion: reduce) { .blob { animation:none; } }
  .glass { background:linear-gradient(160deg, var(--glass), color-mix(in srgb, var(--glass) 55%, transparent));
    -webkit-backdrop-filter:blur(16px) saturate(150%); backdrop-filter:blur(16px) saturate(150%);
    border:1px solid var(--glass-edge); box-shadow:var(--shadow); border-radius:20px; }
  header { position:sticky; top:0; z-index:10; margin:18px 20px 26px; padding:14px 20px;
    display:flex; align-items:center; gap:14px; flex-wrap:wrap; border-radius:16px; }
  header .brand { display:flex; align-items:center; gap:12px; margin-right:auto; }
  header h1 { font-size:18px; font-weight:650; margin:0; letter-spacing:.2px; }
  .pdot { width:9px; height:9px; border-radius:50%; background:#f87171; box-shadow:0 0 0 0 rgba(248,113,113,.5); }
  .pdot.ok { background:var(--ok); box-shadow:0 0 0 0 rgba(52,211,153,.55); animation:pulse 2.2s infinite; }
  @keyframes pulse { 70% { box-shadow:0 0 0 9px rgba(52,211,153,0); } 100% { box-shadow:0 0 0 0 rgba(52,211,153,0); } }
  .pill { display:inline-flex; align-items:center; gap:8px; font-size:12.5px; color:var(--muted);
    background:var(--chip); border:1px solid var(--chip-line); padding:6px 12px; border-radius:999px; }
  .seg { display:flex; flex-wrap:wrap; gap:4px; padding:4px; border:1px solid var(--glass-edge);
    -webkit-backdrop-filter:blur(12px) saturate(150%); backdrop-filter:blur(12px) saturate(150%);
    background:var(--chip); border-radius:999px; }
  .seg button { border:0; background:transparent; color:var(--muted); font-size:12.5px; cursor:pointer;
    padding:7px 13px; border-radius:999px; transition:all .16s ease; font-family:inherit; }
  .seg button:hover { color:var(--fg); }
  .seg button.on { background:linear-gradient(120deg, var(--acc-from), var(--acc-to)); color:var(--acc-fg); font-weight:600; box-shadow:0 4px 14px color-mix(in srgb, var(--acc-from) 45%, transparent); }
  main { max-width:1020px; margin:0 auto; padding:4px 20px 70px; }
  .grid { display:grid; grid-template-columns:1fr; gap:18px; }
  @media (min-width:980px) { .grid { grid-template-columns:1fr 1.35fr; } .try span.tall { grid-column:1 / -1; } }
  .card { padding:22px 22px; }
  .card h2 { font-size:12.5px; margin:0 0 14px; text-transform:uppercase; letter-spacing:1.6px; color:var(--muted); font-weight:600; }
  .gap { display:flex; flex-direction:column; gap:18px; }
  .mono, code, pre { font-family:"Cascadia Mono",Consolas,"Courier New",monospace; }
  .tok { display:flex; gap:8px; align-items:center; margin-bottom:6px; }
  .tok code { flex:1; background:var(--chip); border:1px solid var(--chip-line); padding:9px 12px; border-radius:12px;
    font-size:13px; word-break:break-all; color:var(--fg); }
  .tok .meta { margin:-1px 0 10px 2px; font-size:12.5px; color:var(--muted); }
  button { font-family:inherit; font-size:13.5px; font-weight:550; cursor:pointer; transition:all .16s ease;
    border-radius:12px; padding:9px 15px; border:1px solid transparent; }
  button:focus-visible { outline:2px solid var(--acc-to); outline-offset:2px; }
  button:disabled { opacity:.45; cursor:wait; }
  .btn { background:linear-gradient(120deg, var(--acc-from), var(--acc-to)); color:var(--acc-fg);
    box-shadow:0 6px 18px color-mix(in srgb, var(--acc-from) 40%, transparent); }
  .btn:hover { transform:translateY(-1px); filter:brightness(1.06); }
  .ghost { background:var(--chip); color:var(--fg); border:1px solid var(--chip-line); }
  .ghost:hover { border-color:var(--acc-to); color:var(--fg); }
  .danger { background:transparent; color:var(--err); border:1px solid color-mix(in srgb, var(--err) 45%, transparent); }
  .danger:hover { background:color-mix(in srgb, var(--err) 12%, transparent); border-color:var(--err); }
  input, select, textarea { font-family:inherit; font-size:14px; color:var(--fg);
    background:var(--chip); border:1px solid var(--chip-line); border-radius:12px; padding:10px 12px; transition:border-color .16s ease; }
  input:focus, select:focus, textarea:focus { outline:none; border-color:var(--acc-to); box-shadow:0 0 0 3px color-mix(in srgb, var(--acc-to) 22%, transparent); }
  input[type=text], input[type=password] { flex:1; min-width:190px; font-family:"Cascadia Mono",Consolas,monospace; }
  select { cursor:pointer; }
  textarea { width:100%; resize:vertical; min-height:86px; }
  .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
  .prov { background:var(--surface); border:1px solid var(--chip-line); border-radius:16px; padding:14px 16px; margin-bottom:12px;
    -webkit-backdrop-filter:blur(8px); backdrop-filter:blur(8px); }
  .prov .name { font-weight:650; font-size:14px; }
  .chip { display:inline-flex; align-items:center; gap:6px; font-size:11.5px; color:var(--muted);
    background:var(--chip); border:1px solid var(--chip-line); padding:3px 9px; border-radius:999px; }
  .chip .dot { width:6px; height:6px; border-radius:50%; background:#f87171; }
  .chip .dot.on { background:var(--ok); }
  .prov .fields { display:grid; gap:10px; margin-top:12px; }
  .prov .fields .lbl { font-size:11.5px; color:var(--muted); margin-bottom:3px; letter-spacing:.3px; }
  .tryrow { margin-top:10px; }
  .console { background:var(--chip); border:1px solid var(--chip-line); border-radius:14px; padding:14px 16px;
    font-size:13.5px; white-space:pre-wrap; word-break:break-word; min-height:60px; margin-top:12px; max-height:260px; overflow:auto; }
  .console.err { background:color-mix(in srgb, var(--err) 10%, transparent); border-color:color-mix(in srgb, var(--err) 45%, transparent); color:var(--err); }
  #toasts { position:fixed; top:76px; left:50%; transform:translateX(-50%); z-index:50; display:flex; flex-direction:column; gap:8px; align-items:center; }
  .toast { padding:11px 18px; border-radius:12px; font-size:13.5px; font-weight:500; max-width:min(92vw,560px);
    background:var(--chip); border:1px solid var(--chip-line); color:var(--fg);
    box-shadow:0 12px 32px rgba(2,6,23,.4); animation:toastin .22s ease; }
  .toast.ok { border-color:color-mix(in srgb, var(--ok) 55%, transparent); color:var(--ok); }
  .toast.err { border-color:color-mix(in srgb, var(--err) 55%, transparent); color:var(--err); }
  @keyframes toastin { from { opacity:0; transform:translateY(-8px); } to { opacity:1; transform:translateY(0); } }
  footer { text-align:center; color:var(--muted); font-size:12px; padding:6px 20px 26px; }
  ::-webkit-scrollbar { width:10px; height:10px; }
  ::-webkit-scrollbar-thumb { background:var(--chip-line); border-radius:8px; border:3px solid transparent; background-clip:content-box; }
</style>
</head>
<body>
<div class="blob b1"></div><div class="blob b2"></div><div class="blob b3"></div>
<header class="glass">
  <div class="brand">
    <h1>CarbonX API Bridge</h1>
    <span class="pill" id="statusline"><span class="pdot" id="dot"></span><span id="statustext">&hellip;</span></span>
  </div>
  <div class="seg" id="themes" role="group" aria-label="Theme">
    <button data-set-theme="dark">Midnight</button>
    <button data-set-theme="light">Light</button>
    <button data-set-theme="ocean">Ocean</button>
    <button data-set-theme="forest">Forest</button>
    <button data-set-theme="sunset">Sunset</button>
  </div>
</header>
<main>
  <div id="toasts"></div>
  <div class="grid">
    <div class="glass card gap">
      <div>
        <h2>Access token <span class="pill" style="margin-left:8px" id="tokencount"></span></h2>
        <div id="tokens"></div>
        <div class="row">
          <button id="newtok" class="ghost">Generate new token</button>
          <span class="pill" id="authtier" style="font-size:11.5px"></span>
        </div>
      </div>
    </div>
    <div class="glass card">
      <h2>Models</h2>
      <div class="row">
        <label class="pill" for="default" style="font-size:12px">Default provider</label>
        <select id="default" style="flex:1; min-width:120px"></select>
        <button id="save" class="btn">Save &amp; apply</button>
        <button id="addbtn" class="ghost">Add provider</button>
      </div>
      <div class="muted" id="modelshint" style="font-size:12.5px; color:var(--muted); margin-top:8px">Paste an API key to add or replace a model's key. Leave a key blank to keep the current one. No restart needed.</div>
      <div id="addwrap" hidden>
        <div class="row" style="margin-top:12px">
          <label class="pill" for="preset" style="font-size:12px">Preset</label>
          <select id="preset" style="flex:1; min-width:140px">
            <option value="">Manual</option>
            <option value="ollama">Ollama (local)</option>
            <option value="lmstudio">LM Studio (local)</option>
            <option value="openrouter">OpenRouter</option>
            <option value="opencode">OpenCode</option>
            <option value="custom">Custom / any endpoint</option>
            <option value="openai">OpenAI</option>
            <option value="anthropic">Anthropic</option>
          </select>
        </div>
        <div class="prov" style="margin-top:10px">
          <div class="fields">
            <div><div class="lbl">Name</div><input type="text" id="nname" placeholder="e.g. ollama, my-llm"></div>
            <div><div class="lbl">Type</div><select id="ntype">
              <option value="custom">custom</option>
              <option value="openai">openai</option>
              <option value="anthropic">anthropic</option>
              <option value="openrouter">openrouter</option>
              <option value="opencode">opencode</option>
              <option value="other">other</option>
              <option value="mock">mock</option>
            </select></div>
            <div id="nstylewrap"><div class="lbl">API style</div><select id="nstyle">
              <option value="openai">openai</option><option value="anthropic">anthropic</option>
            </select></div>
            <div id="nsesswrap" hidden><div class="lbl">x-opencode-session</div><input type="password" id="nsess" autocomplete="off"></div>
            <div><div class="lbl">Base URL</div><input type="text" id="nbase" placeholder="http://localhost:11434/v1"></div>
            <div><div class="lbl">API key (optional)</div><input type="password" id="nkey" autocomplete="off"></div>
            <div><div class="lbl">Default model</div><input type="text" id="nmodel" placeholder="e.g. deepseek/deepseek-r1:free"></div>
          </div>
          <div class="row" style="margin-top:12px">
            <button id="addsave" class="btn">Add provider</button>
            <button id="addcancel" class="ghost">Cancel</button>
          </div>
        </div>
      </div>
      <div id="provs" style="margin-top:12px"></div>
    </div>
  </div>
  <div class="glass card try tall" style="margin-top:18px">
    <h2>Try it now <span class="pill" style="margin-left:8px; text-transform:none; letter-spacing:0; font-size:11.5px">uses the default provider's default model</span></h2>
    <textarea id="try" placeholder="Ask the bridge something&hellip;"></textarea>
    <div class="row tryrow">
      <button id="trysend" class="btn">Send</button>
      <span class="pill" id="trystate"></span>
    </div>
    <pre class="console" id="triesult"></pre>
  </div>
</main>
<footer>Loopback-only panel &middot; works on this machine &middot; keys never leave it</footer>
<script>
(window.__errs = []);
window.addEventListener("error", (e) => { window.__errs.push(String(e.message)); toast(e.message || "Script error", "err"); });
window.addEventListener("unhandledrejection", (e) => { window.__errs.push(String((e.reason && e.reason.message) || e.reason)); toast("Request failed: " + ((e.reason && e.reason.message) || "unknown"), "err"); });

const $ = (id) => document.getElementById(id);
let CONFIG = { providers: {}, default_provider: "" };

function toast(msg, kind) {
  const box = $("toasts");
  const el = document.createElement("div");
  el.className = "toast " + (kind || "ok");
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; }, 4800);
  setTimeout(() => el.remove(), 5200);
}

async function api(method, path, body) {
  const r = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((data.error && data.error.message) || ("HTTP " + r.status));
  return data;
}

async function refreshStatus() {
  try {
    const h = await api("GET", "/health");
    $("dot").className = "pdot ok";
    $("statustext").textContent = "v" + h.version + " \u00b7 " + h.providers.join(", ") + " \u00b7 auth " + (h.require_auth ? "on" : "\u26a0 OFF");
  } catch (e) { $("dot").className = "pdot"; $("statustext").textContent = "offline \u2014 is the bridge running?"; }
}

async function loadTokens() {
  try {
    const d = await api("GET", "/ui/token");
    const toks = d.tokens || [];
    $("tokens").innerHTML = toks.length
      ? toks.map((t) =>
        '<div class="tok"><code>' + t.token + '</code>' +
        '<button class="ghost" data-copy>Copy</button>' +
        '<button class="danger" data-del="' + t.token + '">Delete</button></div>' +
        '<div class="meta">user: ' + t.user + ' \u00b7 tier: ' + t.tier + '</div>').join("")
      : '<div class="pill" style="font-size:12.5px">No tokens configured \u2014 generate one below.</div>';
    $("tokencount").textContent = toks.length + " token" + (toks.length === 1 ? "" : "s");
    $("authtier").textContent = toks[0] ? "highest tier: " + toks[0].tier : "";
  } catch (e) { toast("Could not load tokens: " + e.message, "err"); }
}

async function loadConfig() {
  try {
    CONFIG = await api("GET", "/ui/config");
    $("default").innerHTML = Object.keys(CONFIG.providers).map((n) =>
      '<option value="' + n + '"' + (n === CONFIG.default_provider ? " selected" : "") + ">" + n + "</option>").join("");
    $("provs").innerHTML = Object.keys(CONFIG.providers).map(buildProviderCard).join("");
    const withKeys = Object.keys(CONFIG.providers).filter((n) => CONFIG.providers[n].has_key).length;
    $("modelshint").textContent = withKeys
      ? (withKeys + " provider key" + (withKeys === 1 ? " is" : "s are") + " set. Keys are never shown again \u2014 the panel only reports whether a key is set.")
      : "Paste an API key below to add or replace a model's key. Leave a key blank to keep the current one. No restart needed.";
  } catch (e) { toast("Could not load config: " + e.message, "err"); }
}

function buildProviderCard(name) {
  const p = CONFIG.providers[name];
  const styleField = (p.type === "custom" || p.type === "other")
    ? '<div><div class="lbl">API style</div><select data-f="api_style"><option value="openai"' + (p.api_style === "openai" ? " selected" : "") + '>openai</option><option value="anthropic"' + (p.api_style === "anthropic" ? " selected" : "") + '>anthropic</option></select></div>'
    : "";
  return '<div class="prov">' +
    '<div class="row"><span class="name">' + name + '</span>' +
    '<span class="chip">' + p.type + '</span>' +
    '<span class="chip"><span class="dot' + (p.has_key ? " on" : "") + '"></span>' + (p.has_key ? "key set" : "no key") + '</span></div>' +
    '<div class="fields">' +
      '<div><div class="lbl">API key</div><input type="password" data-f="api_key" placeholder="' + (p.has_key ? "key already set \u2014 blank keeps it" : "paste key here") + '"></div>' +
      '<div><div class="lbl">Base URL</div><input type="text" data-f="base_url" value="' + (p.base_url || "") + '"></div>' +
      '<div><div class="lbl">Default model</div><input type="text" data-f="default_model" value="' + (p.default_model || "") + '"></div>' +
      styleField +
    '</div></div>';
}

async function copyText(txt) {
  try { await navigator.clipboard.writeText(txt); return true; }
  catch (e) {
    const ta = document.createElement("textarea");
    ta.value = txt; document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); return true; } catch (e2) { return false; } finally { ta.remove(); }
  }
}

async function delToken(tok) {
  try {
    await api("POST", "/ui/token", { action: "delete", token: tok });
    toast("Token deleted."); await loadTokens(); await refreshStatus();
  } catch (e) { toast(e.message, "err"); }
}

async function genToken() {
  const btn = $("newtok");
  btn.disabled = true; btn.textContent = "Generating\u2026";
  try {
    const d = await api("POST", "/ui/token", {});
    toast("New token created: " + d.token);
    await loadTokens(); await refreshStatus();
  } catch (e) { toast(e.message, "err"); }
  finally { btn.disabled = false; btn.textContent = "Generate new token"; }
}

async function saveConfig() {
  const btn = $("save");
  btn.disabled = true; btn.textContent = "Saving\u2026";
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
    toast("Saved and applied." + ((d.note) ? " " + d.note : ""));
    await loadConfig(); await loadTokens(); await refreshStatus();
  } catch (e) { toast(e.message, "err"); }
  finally { btn.disabled = false; btn.textContent = "Save & apply"; }
}

async function tryChat() {
  const text = $("try").value.trim();
  if (!text) { toast("Type a message first.", "err"); return; }
  const btn = $("trysend");
  btn.disabled = true; $("trystate").textContent = "thinking\u2026";
  try {
    const t = await api("GET", "/ui/token");
    const tok = (t.tokens && t.tokens[0]) ? t.tokens[0].token : null;
    if (!tok) throw new Error("No access token configured on this machine.");
    const r = await fetch("/v1/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + tok },
      body: JSON.stringify({ messages: [{ role: "user", content: text }] })
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((data.error && data.error.message) || ("HTTP " + r.status));
    const out = $("triesult");
    out.className = "console";
    out.textContent = data.content || JSON.stringify(data, null, 2);
  } catch (e) {
    const out = $("triesult");
    out.className = "console err";
    out.textContent = "Error: " + e.message;
  } finally { btn.disabled = false; $("trystate").textContent = ""; }
}

// ---- add provider ----
const PRESETS = {
  ollama:    { name: "ollama",    type: "custom",    style: "openai",    base: "http://localhost:11434/v1", model: "llama3.1" },
  lmstudio:  { name: "lmstudio",  type: "custom",    style: "openai",    base: "http://localhost:1234/v1",   model: "" },
  openrouter:{ name: "openrouter",type: "openrouter",style: "openai",    base: "https://openrouter.ai/api/v1", model: "" },
  opencode:  { name: "opencode",  type: "opencode",  style: "openai",    base: "",                            model: "" },
  custom:    { name: "",           type: "custom",    style: "openai",    base: "",                            model: "" },
  openai:    { name: "openai",    type: "openai",    style: "openai",    base: "",                            model: "gpt-4o-mini" },
  anthropic: { name: "anthropic", type: "anthropic", style: "anthropic", base: "",                            model: "claude-3-5-sonnet-latest" },
};
function syncAddForm() {
  const t = $("ntype").value;
  $("nstylewrap").hidden = !(t === "custom" || t === "other");
  $("nsesswrap").hidden = t !== "opencode";
}
$("addbtn").addEventListener("click", () => { $("addwrap").hidden = false; $("nname").focus(); });
$("addcancel").addEventListener("click", () => { $("addwrap").hidden = true; });
$("preset").addEventListener("change", () => {
  const p = PRESETS[$("preset").value];
  if (!p) return;
  $("nname").value = p.name; $("ntype").value = p.type; $("nstyle").value = p.style;
  $("nbase").value = p.base; $("nmodel").value = p.model; $("nkey").value = ""; $("nsess").value = "";
  syncAddForm();
});
$("ntype").addEventListener("change", syncAddForm);
$("addsave").addEventListener("click", async () => {
  const name = $("nname").value.trim().toLowerCase();
  if (!/^[a-z0-9_-]{1,32}$/.test(name)) { toast("Name: letters, digits, _ or - (max 32)", "err"); return; }
  if (CONFIG.providers[name]) { toast("Provider '" + name + "' already exists \u2014 edit its card instead.", "err"); return; }
  const t = $("ntype").value;
  const fields = { type: t };
  if (t === "custom" || t === "other") fields.api_style = $("nstyle").value;
  if (t === "opencode") { const s = $("nsess").value.trim(); if (s) fields.x_opencode_session = s; }
  const base = $("nbase").value.trim(); if (base) fields.base_url = base;
  const key = $("nkey").value.trim(); if (key) fields.api_key = key;
  const model = $("nmodel").value.trim(); if (model) fields.default_model = model;
  try {
    const o = {}; o[name] = fields;
    await api("POST", "/ui/config", { providers: o });
    toast("Provider '" + name + "' added."); $("addwrap").hidden = true;
    await loadConfig();
  } catch (e) { toast(e.message, "err"); }
});

// ---- wire up ----
$("newtok").addEventListener("click", genToken);
$("save").addEventListener("click", saveConfig);
$("trysend").addEventListener("click", tryChat);
$("try").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) tryChat(); });

document.addEventListener("click", (e) => {
  const copyBtn = e.target.closest("[data-copy]");
  if (copyBtn) {
    const txt = copyBtn.parentElement.querySelector("code").textContent;
    copyText(txt).then((ok) => toast(ok ? "Copied." : "Press Ctrl+C on the selected text."));
    return;
  }
  const del = e.target.closest("[data-del]");
  if (del) delToken(del.getAttribute("data-del"));
});

document.querySelectorAll("[data-set-theme]").forEach((b) => {
  b.addEventListener("click", () => {
    const t = b.getAttribute("data-set-theme");
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("carbonx-theme", t); } catch (e) {}
    document.querySelectorAll("[data-set-theme]").forEach((x) => x.classList.toggle("on", x === b));
  });
});

(function applyThemeControl() {
  const cur = document.documentElement.getAttribute("data-theme") || "dark";
  document.querySelectorAll("[data-set-theme]").forEach((b) => {
    b.classList.toggle("on", b.getAttribute("data-set-theme") === cur);
  });
})();

refreshStatus(); loadTokens(); loadConfig();
</script>
</body>
</html>
"""