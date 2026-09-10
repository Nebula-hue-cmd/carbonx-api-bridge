// Headless-browser test that PROVES the control panel is interactive:
// clicks the real buttons in a real browser (Edge via CDP) and checks results.
// Covers the dual-tab layout, markdown rendering + copy-code, session
// sidebar persistence, the game-context pill and the 8-theme engine.
//
// Usage:  node scripts/panel_test.mjs <panelUrl>  (bridge must be running)
import { spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const EDGE =
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const panelUrl = process.argv[2] || "http://127.0.0.1:8794/";
const PORT = 9333;

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log((ok ? "PASS" : "FAIL") + "  " + name + (detail ? "  [" + detail + "]" : ""));
};

const watchdog = setTimeout(() => { console.log("WATCHDOG TIMEOUT"); process.exit(4); }, 90000);

async function step(label, fn) {
  console.log("[>] " + label);
  return await fn();
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitForJson(url, tries = 40) {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(url);
      if (r.ok) return await r.json();
    } catch {}
    await sleep(250);
  }
  throw new Error("devtools endpoint not ready: " + url);
}

class Cdp {
  static async connect(url) {
    const ws = new WebSocket(url);
    const timeout = setTimeout(() => { try { ws.close(); } catch {} }, 5000);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = (e) => rej(new Error("ws error: " + (e.message || "unknown"))); ws.onclose = () => rej(new Error("ws closed")); });
    clearTimeout(timeout);
    return new Cdp(ws);
  }
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); this.events = new Map();
    ws.addEventListener("message", (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id !== undefined) {
        const p = this.pending.get(m.id);
        if (p) { this.pending.delete(m.id); m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result); }
      } else if (m.method && this.events.has(m.method)) {
        (this.events.get(m.method) || []).forEach((cb) => cb(m.params));
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => { if (this.pending.has(id)) { this.pending.delete(id); reject(new Error("CDP timeout: " + method)); } }, 8000);
    });
  }
  on(event, cb) { if (!this.events.has(event)) this.events.set(event, []); this.events.get(event).push(cb); }
}

// deterministic markdown reply, split across two stream deltas to prove
// piecewise accumulation. Covers markdown + rich blocks: callout, table,
// hex swatches and syntax-colored lua.
const MD_PIECE1 = "**Bold** and `inline_code`.\n\n";
const MD_PIECE2 = "```lua\nprint(\"hello from mock\")\n```\n\nDone.\n\n> [!NOTE] Streamed over **mock**.\n\nSwatch: #16a34a.\n\n| Name | Role |\n| --- | --- |\n| Alby | Hero |\n| Mara | Healer |\n\n```luau\nlocal hp = 100 -- health\nprint('hello from mock 2')\n```";

let edge = null;
try {
  const profile = mkdtempSync(join(tmpdir(), "cbp-"));
  edge = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--remote-debugging-port=" + PORT,
    "--remote-allow-origins=*",
    "--user-data-dir=" + profile,
    "about:blank",
  ], { stdio: "ignore" });

  const tabs = await step("wait for devtools", () => waitForJson(`http://127.0.0.1:${PORT}/json/list`));
  const target = tabs.find((t) => t.type === "page") || tabs[0];
  if (!target) throw new Error("no page target");

  const cdp = await step("connect websocket", async () => Cdp.connect(target.webSocketDebuggerUrl));
  await step("enable Page+Runtime", async () => { await cdp.send("Page.enable"); await cdp.send("Runtime.enable"); });

  let jsErrors = [];
  cdp.on("Runtime.consoleAPICalled", (p) => {
    if (p.type === "error") jsErrors.push(p.args.map((a) => a.value || a.description || "").join(" "));
  });
  cdp.on("Runtime.exceptionThrown", (p) => jsErrors.push((p.exceptionDetails && p.exceptionDetails.text) || "exception"));

  await step("navigate to panel", async () => { await cdp.send("Page.navigate", { url: panelUrl }); await sleep(2500); });

  const evalJS = async (expr) => {
    const r = await cdp.send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error("eval: " + (r.exceptionDetails.text || "exception") + " " + JSON.stringify(r.exceptionDetails.exception || {}));
    return r.result.value;
  };

  const wait = async (expr, ms = 6000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) {
      const v = await evalJS(expr).catch(() => null);
      if (v) return v;
      await sleep(200);
    }
    return null;
  };

  // remember the mock knobs / default provider so we can restore them later
  const original = await evalJS(`fetch('/ui/config').then(r=>r.json()).then(()=>({}))`);
  original.providers = JSON.parse(await (await import("node:fs")).readFileSync("config.json", "utf8")).providers || {};
  original.default_provider = JSON.parse(await (await import("node:fs")).readFileSync("config.json", "utf8")).default_provider;

  // ---- boot ----
  const statusText = await step("wait for statusline", () => wait(`(()=>{const e=document.getElementById('statustext');return e&&e.textContent.trim()!=='connecting…'?e.textContent.trim():null;})()`));
  check("page boots + /health loads (statusline populated)", !!statusText && /v1\.4\.0/.test(statusText), statusText);
  check("no script errors during boot", (await evalJS("window.__errs && window.__errs.length")) === 0);
  check("default tab is Config, Chat page hidden", (await evalJS("document.getElementById('tab-config').hidden === false && document.getElementById('tab-chat').hidden === true")));
  check("hidden tab truly not rendered (computed display none)", (await evalJS("getComputedStyle(document.getElementById('tab-chat')).display")) === "none");

  // ---- config tab primitives ----
  const toks = await step("wait for token rows", () => wait(`document.querySelectorAll('.tok').length >= 1`));
  check("token list rendered in the DOM", !!toks);
  check("theme dropdown lists all 8 themes", (await evalJS(`[...document.querySelectorAll('#themeSel option')].map(o=>o.value).join(',')`)) === "midnight,light,ocean,forest,sunset,cyberpunk,dracula,nord");
  check("every native <select> is replaced by a custom dropdown", (await evalJS("document.querySelectorAll('select').length === document.querySelectorAll('.selwrap').length")));
  check("green X logo rendered (modern brand mark)", (await evalJS(`(()=>{const m=document.querySelector('.brand .mark');if(!m)return false;const cs=getComputedStyle(m,'::before');return cs.width==='4px'&&cs.height==='15px';})()`)) === true);

  // ---- theme engine (select-driven, persisted) ----
  await evalJS(`(()=>{const s=document.getElementById('themeSel'); s.value='light'; s.dispatchEvent(new Event('change'));})()`);
  await sleep(250);
  check("theme select switches data-theme to 'light'", (await evalJS("document.documentElement.getAttribute('data-theme')")) === "light");
  await evalJS(`(()=>{const s=document.getElementById('themeSel'); s.value='cyberpunk'; s.dispatchEvent(new Event('change'));})()`);
  await sleep(250);
  check("theme select switches to 'cyberpunk'", (await evalJS("document.documentElement.getAttribute('data-theme')")) === "cyberpunk");
  // ---- custom dropdown: open the theme menu, click an option ----
  await evalJS(`document.querySelector('#themeSel').parentNode.querySelector('.selbtn').click()`);
  await sleep(120);
  const menuOpen = await evalJS(`document.querySelector('#themeSel').parentNode.querySelector('.selmenu').classList.contains('open')`);
  const menuItems = await evalJS(`[...document.querySelector('#themeSel').parentNode.querySelectorAll('.selitem')].map(x=>x.textContent).join(',')`);
  check("custom dropdown opens with 8 themed options", menuOpen === true && menuItems === "Midnight,Light,Ocean,Forest,Sunset,Cyberpunk,Dracula,Nord", menuItems);
  await evalJS(`[...document.querySelector('#themeSel').parentNode.querySelectorAll('.selitem')].find(x=>x.textContent==='Ocean').click()`);
  await sleep(120);
  check("clicking a dropdown option applies it (data-theme = ocean)", (await evalJS("document.documentElement.getAttribute('data-theme')")) === "ocean");
  check("dropdown option updates the hidden select + menu closes", (await evalJS(`document.getElementById('themeSel').value==='ocean' && !document.querySelector('#themeSel').parentNode.querySelector('.selmenu').classList.contains('open')`)));
  check("dropdown button label updates instantly after pick", (await evalJS(`document.querySelector('#themeSel').parentNode.querySelector('.selval').textContent`)) === "Ocean");
  check("closed menu is not poisoned by an inline pointer-events override", (await evalJS(`document.querySelector('#themeSel').parentNode.querySelector('.selmenu').style.pointerEvents === ''`)));
  await evalJS(`document.querySelector('#themeSel').parentNode.querySelector('.selbtn').click()`);
  await sleep(150);
  const menuAnchor = await evalJS(`(()=>{const w=document.querySelector('#themeSel').parentNode;const b=w.querySelector('.selbtn').getBoundingClientRect();const m=w.querySelector('.selmenu').getBoundingClientRect();return {ok: Math.abs(m.left-b.left)<4 && m.top>=b.bottom-2, dLeft:m.left-b.left, dTop:m.top-b.bottom};})()`);
  check("dropdown menu anchored under its button (no teleport)", menuAnchor.ok === true, "dLeft=" + menuAnchor.dLeft + " dTop=" + menuAnchor.dTop);
  check("dropdown reopens cleanly after being closed once", (await evalJS(`document.querySelector('#themeSel').parentNode.querySelector('.selmenu').classList.contains('open')`)));
  await evalJS(`[...document.querySelector('#themeSel').parentNode.querySelectorAll('.selitem')].find(x=>x.textContent==='Forest').click()`);
  check("second dropdown interaction still selects (no poison)", (await evalJS("document.documentElement.getAttribute('data-theme')")) === "forest");
  await evalJS(`(()=>{const s=document.getElementById('themeSel'); s.value='midnight'; s.dispatchEvent(new Event('change'));})()`);
  await sleep(200);

  // ---- token generation ----
  await evalJS("document.getElementById('newtok').click()");
  const created = await wait(`(document.querySelector('#toasts .toast:last-child')||{}).textContent || ''`, 6000);
  check("Generate new token shows a toast with the token", /New token created/.test(created), created);
  const tokCount1 = await evalJS("document.querySelectorAll('.tok').length");
  check("token list grew by one", tokCount1 >= 2, "count=" + tokCount1);
  const tokStart = tokCount1 - 1;
  const tokCountLabel = await evalJS("document.getElementById('tokencount').textContent");
  check("token count badge matches the rows", new RegExp("^" + tokCount1 + " tokens").test(tokCountLabel), tokCountLabel);

  // ---- pin default provider to mock BEFORE chatting (deterministic stream) ----
  await step("pin default provider to mock (panel save flow)", async () => {
    await evalJS(`(()=>{const sel=document.getElementById('default'); sel.value='mock';})()`);
    await evalJS("document.getElementById('save').click()");
    await sleep(1200);
  });
  check("Save & apply shows a success toast", /Saved/.test(await evalJS("(document.querySelector('#toasts .toast:last-child')||{}).textContent || ''")));
  check("default select now shows mock", (await evalJS("document.getElementById('default').value")) === "mock");

  // ---- point the mock provider at a markdown reply (mock-only knobs) ----
  await step("inject mock markdown reply via /ui/config", async () => {
    const mockBody = JSON.stringify({ providers: { mock: { reply: MD_PIECE1 + MD_PIECE2, stream_pieces: [MD_PIECE1, MD_PIECE2] } } });
    await evalJS("fetch('/ui/config',{method:'POST',headers:{'Content-Type':'application/json'},body:" + JSON.stringify(mockBody) + "})");
    await sleep(500);
  });

  // ---- switch to chat tab ----
  await step("open Chat tab", async () => { await evalJS("document.getElementById('tbChat').click()"); await sleep(400); });
  check("Chat tab active, Config page hidden", (await evalJS("document.getElementById('tab-chat').hidden === false && document.getElementById('tab-config').hidden === true")));
  const curSessBoot = await evalJS("document.getElementById('currsession').textContent");
  const sessBoot = await evalJS("document.querySelectorAll('#sessions .session').length");
  check("a default session exists (New session)", sessBoot === 1 && curSessBoot === "New session", curSessBoot);

  // ---- multi-turn chat with markdown rendering ----
  await evalJS(`document.getElementById('chatinput').value = 'is this real?';`);
  await evalJS("document.getElementById('chatsend').click()");
  const aiBubble = await wait(`(()=>{const m=[...document.querySelectorAll('.bubble.assistant')].pop();return m&&!m.classList.contains('streaming')?m:null;})()`, 15000);
  check("chat streams the mock reply into a bubble", !!aiBubble);
  check("markdown bold + inline code rendered", (await evalJS(`(()=>{const b=document.querySelector('.bubble.assistant');return !!(b.querySelector('strong')&&b.querySelector('strong').textContent==='Bold'&&b.querySelector('p code')&&b.querySelector('p code').textContent==='inline_code');})()`)));
  check("fenced code block rendered with language label", (await evalJS(`(()=>{const b=document.querySelector('.bubble.assistant .codeblock');return !!b&&b.querySelector('.cbhead code').textContent==='lua';})()`)));
  const codeTxt = await evalJS("(document.querySelector('.bubble.assistant .codeblock pre code')||{}).textContent");
  check("code content intact inside the block", codeTxt === 'print("hello from mock")', codeTxt);

  await evalJS(`window.__copies=[];window.copyText=function(t){window.__copies.push(t);return navigator.clipboard.writeText(t);};document.querySelector('.bubble.assistant .codeblock .copycode').click();`);
  await wait(`window.__copies && window.__copies.length`);
  check("Copy-code button copies the fenced block", (await evalJS("window.__copies.join('|')")).indexOf('print("hello from mock")') >= 0, (await evalJS("window.__copies[0]")));

  // ---- rich display: callout, table, color chip, highlighted luau ----
  const rich = await evalJS(`(()=>{const b=document.querySelector('.bubble.assistant');return {
    callout: !!b.querySelector('.callout.note') && (b.querySelector('.callout.note .ct')||{}).textContent,
    table: b.querySelector('table.md') ? b.querySelectorAll('table.md tbody tr').length : 0,
    chip: (b.querySelector('.chip .sw')||{}).getAttribute ? b.querySelector('.chip').textContent : '',
    chips: b.querySelectorAll('.chip').length,
    tk: b.querySelectorAll('.codeblock .tk-k, .codeblock .tk-s, .codeblock .tk-n').length,
    lastLang: [...b.querySelectorAll('.codeblock .cbhead code')].pop() ? [...b.querySelectorAll('.codeblock .cbhead code')].pop().textContent : ''
  };})()`);
  check("callout box rendered from > [!NOTE]", rich.callout === "Note", rich.callout);
  check("markdown table rendered with 2 data rows", rich.table === 2, "rows=" + rich.table);
  check("hex color rendered as a swatch chip", rich.chips >= 1 && /#16a34a/.test(rich.chip || ""), rich.chip);
  check("lua tokens syntax-colored inside the code block", rich.tk >= 3, "tokens=" + rich.tk);
  check("second code block has its luau language label", rich.lastLang === "luau", rich.lastLang);
  await evalJS(`window.__copies=[];const blocks=[...document.querySelectorAll('.bubble.assistant .codeblock')];blocks[1].querySelector('.copycode').click();`);
  await wait(`window.__copies && window.__copies.length`);
  check("copy from the highlighted block preserves exact text", (await evalJS("window.__copies.join('\\n')")).indexOf("print('hello from mock 2')") >= 0, await evalJS("window.__copies[0]"));

  // ---- copy-raw-markdown button on assistant bubbles ----
  const copymdBtn = await evalJS(`!!document.querySelector('.bubble.assistant .copymd')`);
  check("assistant bubble has a copy-raw-markdown button", copymdBtn);
  await evalJS(`window.__copies=[];document.querySelector('.bubble.assistant .copymd').click();`);
  await wait(`window.__copies && window.__copies.length`);
  const copiedMd = await evalJS("window.__copies[0]||''");
  check("copy-raw-markdown copies markdown source text", copiedMd.length > 20 && /\\*\\*Bold\\*\\*/.test(copiedMd), "len=" + copiedMd.length);

  // ---- callout copy button ----
  const copycallBtn = await evalJS(`!!document.querySelector('.bubble.assistant .callout .copycall')`);
  check("callout box has a copy button", copycallBtn);
  await evalJS(`window.__copies=[];document.querySelector('.bubble.assistant .callout .copycall').click();`);
  await wait(`window.__copies && window.__copies.length`);
  const copiedCallout = await evalJS("window.__copies[0]||''");
  check("callout copy outputs GitHub-style callout markdown", copiedCallout.indexOf('[!NOTE]') >= 0, copiedCallout.slice(0, 80));

  const been2 = await evalJS("document.querySelectorAll('.bubble').length");
  check("user + assistant bubbles kept in history", been2 >= 2, "bubbles=" + been2);
  check("session titled from the first message", (await evalJS("document.querySelector('.session .t').textContent")) === "is this real?");

  await evalJS(`document.getElementById('chatinput').value = 'second message';`);
  await evalJS("document.getElementById('chatsend').click()");
  await wait(`(()=>{const m=[...document.querySelectorAll('.bubble.assistant')].pop();return m&&!m.classList.contains('streaming');})()`, 15000);
  const historyAfter = await evalJS("document.querySelectorAll('.bubble').length");
  check("second turn extends the same history", historyAfter >= been2 + 2, "bubbles=" + historyAfter);

  // ---- reload: sessions + chat history survive (localStorage) ----
  await step("reload page", async () => { await cdp.send("Page.reload", { ignoreCache: true }); await sleep(2500); });
  const restoredBubbles = await step("reload restores chat history", () => wait(`document.querySelectorAll('.bubble.assistant').length >= 2`, 10000));
  check("chat history survives a reload and re-renders markdown", !!restoredBubbles && (await evalJS("document.querySelector('.bubble.assistant .codeblock pre code')||false")) !== false);
  check("sessions persisted across reload", (await evalJS("document.querySelectorAll('#sessions .session').length")) === 1);
  check("theme persisted across reload (midnight)", (await evalJS("document.documentElement.getAttribute('data-theme')")) === "midnight");

  // restore the pre-test mock knobs + default provider (best-effort, right after chat)
  const restoreBody = JSON.stringify({
    default_provider: original.default_provider,
    providers: { mock: { reply: (original.providers.mock || {}).reply, stream_pieces: (original.providers.mock || {}).stream_pieces } },
  });
  await evalJS("fetch('/ui/config',{method:'POST',headers:{'Content-Type':'application/json'},body:" + JSON.stringify(restoreBody) + "})");

  // ---- session sidebar: new / switch / delete ----
  await evalJS("document.getElementById('newchat').click()");
  await sleep(300);
  const afterNew = await evalJS("document.querySelectorAll('#sessions .session').length");
  check("New session adds a sidebar entry", afterNew === 2, "sessions=" + afterNew);
  check("new session starts empty (idle prompt)", (await evalJS("document.querySelector('#msgs .idle')!==null")));
  await evalJS("[...document.querySelectorAll('#sessions .session')].find(s=>s.className.indexOf('session')>=0&&s.querySelector('.t').textContent!=='New session').click()");
  await sleep(400);
  check("switching to the old session restores its bubbles", (await evalJS("document.querySelectorAll('.bubble.assistant').length")) >= 2);
  await evalJS("document.querySelectorAll('#sessions .session')[0].click()");
  await sleep(300);
  check("switching back re-shows the idle prompt", (await evalJS("document.querySelector('#msgs .idle')!==null")));
  await evalJS("document.querySelectorAll('#sessions .session')[1].querySelector('.del').click()");
  await sleep(400);
  check("deleting a session removes it from the sidebar", (await evalJS("document.querySelectorAll('#sessions .session').length")) === 1);

  // ---- game-context pill (chat head) ----
  await evalJS("document.getElementById('tbConfig').click()");
  await sleep(300);
  const pushOk = await evalJS(`(async()=>{const t=await fetch('/ui/token').then(r=>r.json());const tok=t.tokens[0].token;const r=await fetch('/v1/game-context',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+tok},body:JSON.stringify({game:'Adopt Me!',players:[{name:'Alice'},{name:'Bob'}],files:[{path:'workspace/Main',content:'local g = game'}]})});return r.ok;})()`);
  check("pushing game context via /v1/game-context succeeds", pushOk === true);
  await evalJS("document.getElementById('tbChat').click()");
  const pillOn = await step("context pill goes active", () => wait(`(()=>{const p=document.getElementById('ctxpill');return p&&p.className.indexOf('on')>=0?p.textContent:null;})()`, 10000));
  check("chat head pill shows Context Active", pillOn === "Context Active", pillOn);
  check("config summary shows the attached game", (/Adopt Me!/.test(await evalJS("document.getElementById('gctxinfo').textContent"))));
  await evalJS("document.getElementById('tbConfig').click()");
  await evalJS("document.getElementById('gctxclear').click()");
  const pillOff = await step("Forget game context disarms the pill", () => wait(`(()=>{const p=document.getElementById('ctxpill');return p&&p.className.indexOf('off')>=0;})()`, 10000));
  check("pill returns to No context after clearing", pillOff === true && (await evalJS("document.getElementById('ctxpill').textContent")) === "No context");

  // ---- Lumen attach card (paste-ready script) ----
  const lscr = await evalJS(`fetch('/ui/lumen-script').then(r=>r.text()).then(t=>({ok:t.includes('BRIDGE_URL')&&t.includes('game-context'),n:t.split('\\n').length}))`);
  check("GET /ui/lumen-script returns the paste-ready script", !!lscr.ok && lscr.n > 200, lscr.n + " lines");
  const lscardCount = await step("Lumen card line count fills in", () => wait(`(document.getElementById('lsccount')||{}).textContent || ''`));
  check("Lumen card shows line count", /lines/.test(lscardCount), lscardCount);
  const lscriptFilled = await evalJS(`(document.getElementById('lscript').textContent||'').includes('BRIDGE_URL = "') && !(document.getElementById('lscript').textContent||'').includes('PASTE-YOUR-BRIDGE-TOKEN-HERE')`);
  check("card script has bridge URL + a real token filled in", lscriptFilled === true);

  // ---- add a provider through the panel UI (Ollama preset) ----
  const ollamaAlready = await evalJS(`!!CONFIG.providers.ollama`);
  if (!ollamaAlready) {
    await evalJS(`document.getElementById('addbtn').click()`);
    await evalJS(`(()=>{const s=document.getElementById('ntype'); const w=s.parentNode.querySelector('.selbtn'); w.click();})()`);
    await sleep(150);
    const ntypeOptions = await evalJS(`[...document.getElementById('ntype').parentNode.querySelectorAll('.selitem')].map(x=>x.textContent).join(',')`);
    check("Add-provider type dropdown lists all provider types", ntypeOptions === "custom,openai,anthropic,openrouter,opencode,mock", ntypeOptions);
    await evalJS(`[...document.getElementById('ntype').parentNode.querySelectorAll('.selitem')].find(x=>x.textContent==='openai').click()`);
    await sleep(100);
    check("Add-provider type dropdown picks a type + label updates", (await evalJS(`document.getElementById('ntype').value`)) === "openai" && (await evalJS(`document.getElementById('ntype').parentNode.querySelector('.selval').textContent`)) === "openai");
    await evalJS(`(()=>{const s=document.getElementById('preset'); s.value='ollama'; s.dispatchEvent(new Event('change'));})()`);
    await evalJS(`document.getElementById('addsave').click()`);
    const cardAdded = await wait(`[...document.querySelectorAll('#provs .name')].some(n=>n.textContent.trim()==='ollama')`, 8000);
    check("Add-provider (Ollama preset) creates its card", !!cardAdded);
    check("add-provider toast shown", /Provider 'ollama' added/.test(await evalJS("(document.querySelector('#toasts .toast:last-child')||{}).textContent || ''")));
  } else {
    check("add-provider (ollama already present from a previous run)", true, "skipped");
  }
  check("ollama provider persisted via /ui/config", (await evalJS(`fetch('/ui/config').then(r=>r.json()).then(d=>d.providers.ollama&&d.providers.ollama.base_url||'')`)) === "http://localhost:11434/v1");
  check("default provider untouched by add flow", (await evalJS(`fetch('/ui/config').then(r=>r.json()).then(d=>d.default_provider)`)) === original.default_provider);

  // ---- persistence + final hygiene ----
  const persist = await evalJS(`fetch('/ui/token').then(r=>r.json()).then(d=>d.tokens.length)`);
  check("new token persisted server-side", persist === tokCount1, "server sees " + persist);
  check("no console errors after all interactions", jsErrors.length === 0, jsErrors.slice(0, 3).join(" | "));
  check("no caught script errors after all interactions", (await evalJS("window.__errs && window.__errs.length")) === 0);

  const failed = results.filter((r) => !r.ok);
  console.log("\n" + (results.length - failed.length) + "/" + results.length + " checks passed");
  process.exit(failed.length ? 1 : 0);
} catch (err) {
  console.log("FATAL: " + err.message);
  process.exit(3);
} finally {
  clearTimeout(watchdog);
  await sleep(300);
  if (edge) edge.kill();
}