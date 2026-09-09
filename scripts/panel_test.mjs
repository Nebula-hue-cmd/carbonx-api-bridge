// Headless-browser test that PROVES the control panel is interactive:
// clicks the real buttons in a real browser (Edge via CDP) and checks results.
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

const watchdog = setTimeout(() => { console.log("WATCHDOG TIMEOUT"); process.exit(4); }, 60000);

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

  const statusText = await step("wait for statusline", () => wait(`(()=>{const e=document.getElementById('statustext');return e&&e.textContent.trim()!=='…'?e.textContent.trim():null;})()`));
  check("page boots + /health loads (statusline populated)", !!statusText && /v1\.3\.0/.test(statusText), statusText);
  check("no script errors during boot", (await evalJS("window.__errs && window.__errs.length")) === 0);

  const toks = await step("wait for token rows", () => wait(`document.querySelectorAll('.tok').length >= 1`));
  check("token list rendered in the DOM", !!toks);

  await evalJS(`[...document.querySelectorAll('[data-set-theme]')].find(b=>b.getAttribute('data-set-theme')==='light').click()`);
  await sleep(300);
  const attr = await evalJS("document.documentElement.getAttribute('data-theme')");
  check("theme click switches data-theme to 'light'", attr === "light", attr);
  await evalJS(`[...document.querySelectorAll('[data-set-theme]')].find(b=>b.getAttribute('data-set-theme')==='ocean').click()`);
  await sleep(300);
  check("theme click switches to 'ocean'", (await evalJS("document.documentElement.getAttribute('data-theme')")) === "ocean");
  await evalJS(`[...document.querySelectorAll('[data-set-theme]')].find(b=>b.getAttribute('data-set-theme')==='dark').click()`);
  await sleep(200);

  await evalJS("document.getElementById('newtok').click()");
  const created = await wait(`(document.querySelector('#toasts .toast:last-child')||{}).textContent || ''`, 6000);
  check("Generate new token shows a toast with the token", /New token created/.test(created), created);
  const tokCount1 = await evalJS("document.querySelectorAll('.tok').length");
  check("token list grew by one", tokCount1 >= 2, "count=" + tokCount1);

  await evalJS(`document.getElementById('try').value = 'is this real?';`);
  await evalJS("document.getElementById('trysend').click()");
  const result = await wait(`document.getElementById('triesult').textContent || ''`, 8000);
  check("Try-it-now returns the mock reply", /mock/.test(result), result.slice(0, 90));

  await evalJS(`(()=>{const sel=document.getElementById('default'); sel.value='mock';})()`);
  await evalJS("document.getElementById('save').click()");
  await sleep(1200);
  check("Save & apply shows a success toast", /Saved/.test(await evalJS("(document.querySelector('#toasts .toast:last-child')||{}).textContent || ''")));

  const persist = await evalJS(`fetch('/ui/token').then(r=>r.json()).then(d=>d.tokens.length)`);
  check("new token persisted server-side", persist === tokCount1, "server sees " + persist);
  check("no console errors after all interactions", jsErrors.length === 0, jsErrors.slice(0, 3).join(" | "));

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