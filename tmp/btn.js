// Два прогона: (A) модель установлена — прямой старт; (B) модели нет —
// клик докачивает и запускает сам, под кнопкой видна причина.
const { JSDOM } = require("jsdom");
const path = require("path");
const fs = require("fs");

const root = path.join(__dirname, "..", "web");
const html = fs.readFileSync(path.join(root, "index.html"), "utf-8");
const appJs = fs.readFileSync(path.join(root, "app.js"), "utf-8");

const HEALTH = {
  ok: true, ffmpeg_version: "ffmpeg version 9 test", cores: 8,
  models_mb: 255.8, free_mb: 18000, version: "1.0",
  devices: { gpus: [{ id: 0, name: "RX 550X", type: "x" }],
    gpu: { available: true, note: "" }, cpus: { available: true } },
  engines: { gpu: { installed: true }, cpu: { installed: true }, srmd: { installed: true } },
};
const SETTINGS = { settings: {
  engine: "gpu", model: "realesr-general-x4v3", scale_mode: "percent",
  percent: 150, width: 1920, height: 1080, keep_width: false, keep_height: true,
  long_edge: 1920, even_only: true, tile: 0, threads: 4, tta: false,
  chunk_seconds: 10, container: "mp4", codec: "libx264", crf: 17,
  crf_mode: "crf", bitrate: 8000, x264_preset: "slow", fps: 0, audio: "copy",
  audio_bitrate: 192, start: 0, duration: 0,
} };
function modelsDoc(installed) {
  return { models: [
    { id: "realesr-general-x4v3", title: "general", summary: "s", scale: 4,
      engines: ["gpu"], size_mb: 2.3, default: true,
      installed: installed, installed_mb: installed ? 2.3 : 0 },
    { id: "srmd-x2", title: "srmd x2", summary: "s", scale: 2,
      engines: ["srmd"], size_mb: 2.9,
      installed: installed, installed_mb: installed ? 2.9 : 0 },
    { id: "srmd-x3", title: "srmd x3", summary: "s", scale: 3,
      engines: ["srmd"], size_mb: 2.9,
      installed: installed, installed_mb: installed ? 2.9 : 0 },
  ] };
}
const PRESETS = { built_in: [] };
const META = { meta: {
  name: "v.mp4", size_bytes: 1000, width: 720, height: 960, fps: 30,
  fps_text: "30/1", duration: 13, frames: 409, video_codec: "h264",
  pix_fmt: "yuv420p", profile: "High", bitrate: 1000000, has_audio: false,
  audio_codec: "", audio_channels: 0, container: "mp4",
  token: "tok1", preview_url: "/api/preview/tok1",
} };
function jobDoc(stage) {
  return { job: { id: "j1", stage: stage, message: stage, percent: stage === "done" ? 100 : 0,
    frame: stage === "done" ? 409 : 0, frames_total: 409, speed: 0, eta: null,
    source: "v.mp4", output: stage === "done" ? "v_up2x.mp4" : null,
    output_size: 999, output_meta: {}, error: null, options: {} } };
}

async function run(initialInstalled, label, actions, opts) {
  opts = opts || {};
  let installedNow = initialInstalled;
  let polls = 0;
  let installRelease = null;
  const api = {
    releaseInstall: function () { if (installRelease) { installRelease(); installRelease = null; } },
  };
  const calls = [];
  const errors = [];
  function route(url, fetchOpts) {
    const m = (fetchOpts && fetchOpts.method) || "GET";
    calls.push(m + " " + String(url).split("?")[0]);
    if (url === "/api/tabs") return { tabs: [{ id: "upscale", title: "U", ready: true }] };
    if (url === "/api/health") return HEALTH;
    if (url === "/api/settings") return SETTINGS;
    if (url === "/api/models") return modelsDoc(installedNow);
    if (url === "/api/presets") return PRESETS;
    if (url === "/api/probe") return META;
    if (url.indexOf("/api/models/") === 0 && m === "POST") {
      if (opts.gateInstall) {
        return new Promise(function (res) {
          installRelease = function () { installedNow = true; res({ state: "done" }); };
        });
      }
      installedNow = true;
      return { state: "done" };
    }
    if (url === "/api/jobs" && m === "POST") { polls = 0; return jobDoc("preparing"); }
    if (url === "/api/jobs/j1") { polls++; return polls < 3 ? jobDoc("preparing") : jobDoc("done"); }
    if (url.indexOf("/logs") >= 0) return { logs: [] };
    if (url.indexOf("/estimate") === 0) return { frames: 409, engine: "gpu", seconds: 224, out_mb: 72 };
    if (m === "PUT") return {};
    throw new Error("unexpected " + m + " " + url);
  }

  const dom = new JSDOM(html, { url: "http://127.0.0.1:8756/", runScripts: "outside-only", pretendToBeVisual: true });
  const { window } = dom;
  window.fetch = (url, opts) => Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve(route(String(url), opts || {})),
  });
  window.addEventListener("error", (e) => errors.push(e.message));
  let tid = 1;
  const timers = new Map();
  window.setTimeout = (fn) => { const id = tid++; timers.set(id, fn); return id; };
  window.clearTimeout = (id) => { timers.delete(id); };
  function pump() {
    for (let r = 0; r < 8; r++) {
      const due = [...timers.values()];
      timers.clear();
      if (!due.length) break;
      due.forEach((fn) => fn());
    }
  }
  async function tick(n) {
    for (let i = 0; i < (n || 12); i++) {
      await Promise.resolve();
      await new Promise((r) => setImmediate(r));
      pump();
    }
  }

  const fails = [];
  const check = (n, c, x) => { console.log((c ? "PASS " : "FAIL ") + "[" + label + "] " + n + (x || "")); if (!c) fails.push(label + ":" + n); };
  try { window.eval(appJs); } catch (e) { console.log("FAIL eval: " + e.message); process.exit(1); }
  const d = window.document;
  await tick(30);
  await actions({ d, window, tick, check, calls, errors, api });
  check("no window errors", errors.length === 0, errors.join("|"));
  return fails;
}

async function uploadFile(ctx) {
  const { d, tick } = ctx;
  const fi = d.getElementById("file");
  const w = d.defaultView;
  Object.defineProperty(fi, "files", { value: [new w.File(["x".repeat(50)], "v.mp4", { type: "video/mp4" })], configurable: true });
  fi.dispatchEvent(new w.Event("change", { bubbles: true }));
  await tick(30);
}

(async () => {
  let fails = [];

  // Прогон A: модель установлена
  fails = fails.concat(await run(true, "IDX", async (ctx) => {
    const { d, tick, check, calls } = ctx;
    const seen = {};
    const dupes = [...d.querySelectorAll("[id]")].map((n) => n.id).filter((id) => seen[id] ? true : !(seen[id] = 1));
    check("no duplicate ids", dupes.length === 0, " dupes=" + JSON.stringify(dupes));
    const rb = d.getElementById("runbtn");
    check("runbtn is BUTTON", !!rb && rb.tagName === "BUTTON", " tag=" + (rb && rb.tagName));
    const ti = d.getElementById("time_start");
    check("time_start is INPUT", !!ti && ti.tagName === "INPUT", " tag=" + (ti && ti.tagName));
  }));

  // Run A
  fails = fails.concat(await run(true, "A", async (ctx) => {
    const { d, tick, check, calls } = ctx;
    const start = d.getElementById("runbtn");
    check("clickable before upload", start.disabled === false);
    check("reason asks for file", d.getElementById("start-why").textContent.length > 5);
    calls.length = 0;
    start.click();
    await tick(20);
    check("no job without file", !calls.some((c) => c === "POST /api/jobs"));
    d.getElementById("stopbtn").click();
    await tick(10);
    check("cancel w/o job sends nothing", !calls.some((c) => c.indexOf("/cancel") >= 0));
    await uploadFile(ctx);
    check("enabled after upload", start.disabled === false);
    await new Promise((r) => setTimeout(r, 1700));
    calls.length = 0;
    start.click();
    await tick(30);
    check("POST jobs sent", calls.some((c) => c === "POST /api/jobs"));
    check("single POST per click (dedup)", calls.filter((c) => c === "POST /api/jobs").length === 1,
      " n=" + calls.filter((c) => c === "POST /api/jobs").length);
    check("progress visible", !d.getElementById("progress").hidden);
  }));

  // Прогон B: модели нет — boot докачивает сам
  fails = fails.concat(await run(false, "B", async (ctx) => {
    const { d, tick, check, calls } = ctx;
    check("boot auto-installs", calls.some((c) => c.indexOf("POST /api/models/") === 0));
    await uploadFile(ctx);
    const start = d.getElementById("runbtn");
    check("clickable after boot-install", start.disabled === false);
    calls.length = 0;
    start.click();
    await tick(30);
    check("direct start", calls.some((c) => c === "POST /api/jobs"));
  }));

  // Прогон D: движок SRMD — свои модели, нативный ×2, без ресайза
  fails = fails.concat(await run(true, "D", async (ctx) => {
    const { d, tick, check, calls } = ctx;
    d.getElementById("mode-adv").click();
    await tick(10);
    check("adv mode on", !d.body.classList.contains("simple"));
    const sr = d.querySelector('input[name="engine"][value="srmd"]');
    check("srmd radio exists+enabled", !!sr && sr.disabled === false);
    d.querySelectorAll('input[name="engine"]').forEach(function (r) { r.checked = false; });
    sr.checked = true;
    sr.dispatchEvent(new d.defaultView.Event("change", { bubbles: true }));
    await tick(20);
    const opts = [...d.getElementById("model").options].map((o) => o.value);
    check("srmd models listed", opts.indexOf("srmd-x2") >= 0 && opts.indexOf("srmd-x3") >= 0,
      " opts=" + JSON.stringify(opts));
    await uploadFile(ctx);
    const pct = d.getElementById("percent");
    pct.value = "200";
    pct.dispatchEvent(new d.defaultView.Event("input", { bubbles: true }));
    await tick(20);
    const engChecked = [...d.querySelectorAll('input[name="engine"]')].filter((r) => r.checked).map((r) => r.value);
    console.log("  DEBUG engine checked=" + JSON.stringify(engChecked) +
      " model.value=" + d.getElementById("model").value);
    const calc = d.getElementById("calc").textContent;
    check("srmd x2 exact target", calc.indexOf("1440") >= 0 && calc.indexOf("1920") >= 0, " calc=" + calc.slice(0, 50));
    const note = d.getElementById("scale-note").textContent;
    check("no lanczos warning", d.getElementById("scale-note").className.indexOf("ok") >= 0);
  }));

  // Прогон C: клик ВО ВРЕМЯ скачивания — докачка + автозапуск, причина видна
  fails = fails.concat(await run(false, "C", async (ctx) => {
    const { d, tick, check, calls, api } = ctx;
    const opt0 = d.getElementById("model").options[0];
    check("DEBUG select shows download tag", opt0 && opt0.textContent.indexOf("(") >= 0, " opt=" + JSON.stringify(opt0 && opt0.textContent));
    check("DEBUG status box visible", !d.getElementById("model-status").hidden);
    await uploadFile(ctx);
    const start = d.getElementById("runbtn");
    const why = d.getElementById("start-why").textContent;
    check("reason visible while downloading", why.length > 5, " why=" + JSON.stringify(why));
    check("clickable while downloading", start.disabled === false);
    calls.length = 0;
    start.click();
    await tick(20);
    const why2 = d.getElementById("start-why").textContent;
    check("reason mentions auto-start", why2.length > 5, " why=" + JSON.stringify(why2));
    check("no job before install done", !calls.some((c) => c === "POST /api/jobs"));
    api.releaseInstall();
    await tick(40);
    check("auto-start after install", calls.some((c) => c === "POST /api/jobs"),
      " " + JSON.stringify(calls.slice(0, 10)));
    check("progress visible", !d.getElementById("progress").hidden);
  }, { gateInstall: true }));

  console.log("FAIL count: " + fails.length);
  process.exit(fails.length ? 1 : 0);
})().catch((e) => { console.log("FAIL harness: " + (e && e.stack || e)); process.exit(1); });
