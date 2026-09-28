// Тесты нового UI: режимы, цели, вкладки, generic-инструмент end-to-end.
const { JSDOM } = require("jsdom");
const path = require("path");
const fs = require("fs");

const root = path.join(__dirname, "..", "web");
const html = fs.readFileSync(path.join(root, "index.html"), "utf-8");
const appJs = fs.readFileSync(path.join(root, "app.js"), "utf-8");

const HEALTH = {
  ok: true, ffmpeg_version: "ffmpeg version 9.0 test", ffmpeg_short: "9.0",
  cores: 8, models_mb: 100, free_mb: 18000, version: "1.0",
  devices: { gpus: [{ id: 0, name: "RX", type: "x" }],
    gpu: { available: true, note: "" }, cpus: { available: true } },
  engines: { gpu: { installed: true } },
};
const SETTINGS = { settings: {
  engine: "gpu", model: "m1", scale_mode: "percent", percent: 200,
  width: 1920, height: 1080, keep_width: false, keep_height: true,
  long_edge: 1920, even_only: true, tile: 0, threads: 4, tta: false,
  chunk_seconds: 10, container: "mp4", codec: "libx264", crf: 17,
  crf_mode: "crf", bitrate: 8000, x264_preset: "slow", fps: 0, audio: "copy",
  audio_bitrate: 192, start: 0, duration: 0,
} };
const MODELS = { models: [
  { id: "m1", title: "m1", summary: "s", scale: 4, engines: ["gpu"],
    size_mb: 2, default: true, installed: true, installed_mb: 2 },
] };
const TABS = { groups: [
  { id: "video", title: "Видео" },
  { id: "files", title: "Файлы" },
], tabs: [
  { id: "upscale", title: "Upscale", ready: true, group: "video", hint: "нейросеть" },
  { id: "v-comp", title: "Compress", ready: true, group: "video" },
  { id: "meta", title: "Meta", ready: true, group: "files" },
  { id: "zz", title: "Soon", ready: false, group: "files" },
] };
const OPSCHEMA = { op: {
  id: "v-comp", title: "Compressor", desc: "d", accept: "video/*",
  kinds: ["video"], submit: "Go",
  fields: [
    { key: "codec", label: "Codec", type: "select",
      options: [{ id: "libx264", title: "H264" }], def: "libx264" },
    { key: "crf", label: "CRF", type: "number", def: 20, min: 0, max: 51, step: 1 },
    { key: "flag", label: "Flag", type: "check", def: false },
  ],
} };
const META = { meta: {
  name: "v.mp4", size_bytes: 100, width: 640, height: 360, fps: 24,
  fps_text: "24/1", duration: 10, frames: 240, video_codec: "h264",
  pix_fmt: "yuv420p", profile: "H", bitrate: 999, has_audio: true,
  audio_codec: "aac", audio_channels: 2, container: "mp4", kind: "video",
  token: "tok9", preview_url: "/api/preview/tok9",
} };
function jobDoc(stage) {
  return { job: { id: "mj1", stage: stage, message: stage, percent: stage === "done" ? 100 : 10,
    frame: 0, frames_total: 1000, speed: 0, eta: null, source: "v.mp4",
    output: stage === "done" ? "v_min.mp4" : null, output_size: 11,
    output_meta: {}, error: null, options: {} } };
}

const calls = [];
const errors = [];
let polls = 0;
function route(url, opts) {
  const m = (opts && opts.method) || "GET";
  calls.push(m + " " + String(url).split("?")[0]);
  if (url === "/api/tabs") return TABS;
  if (url === "/api/health") return HEALTH;
  if (url === "/api/settings") return SETTINGS;
  if (url === "/api/models") return MODELS;
  if (url === "/api/presets") return { built_in: [] };
  if (url === "/api/media/ops/v-comp") return OPSCHEMA;
  if (url === "/api/media/probe") return META;
  if (url === "/api/media/jobs" && m === "POST") { polls = 0; return jobDoc("encoding"); }
  if (url === "/api/jobs/mj1") { polls++; return polls < 2 ? jobDoc("encoding") : jobDoc("done"); }
  if (url.indexOf("/logs") >= 0) return { logs: [] };
  if (url.indexOf("/estimate") === 0) return { frames: 1, engine: "gpu", seconds: 1, out_mb: 1 };
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

(async () => {
  const fails = [];
  const check = (n, c, x) => { console.log((c ? "PASS " : "FAIL ") + n + (x || "")); if (!c) fails.push(n); };
  try { window.eval(appJs); } catch (e) { console.log("FAIL eval: " + e.message); process.exit(1); }
  const d = window.document;
  await tick(30);

  check("booted", window.__booted === true);
  check("simple by default", d.body.classList.contains("simple"));
  check("mode seg", !!d.getElementById("mode-simple") && !!d.getElementById("mode-adv"));
  check("13ish tabs", d.querySelectorAll("#tablist .tab").length === 4);
  check("health short", d.getElementById("health").textContent.indexOf("9.0") >= 0);
  check("health threads", d.getElementById("health").textContent.indexOf("8") >= 0);
  check("goals rendered", d.querySelectorAll("#goalbtns button").length === 5);
  check("adv hidden in simple",
    d.getElementById("sec-out").classList.contains("adv-only") &&
    d.getElementById("sec-engine").querySelector("#engine-row").classList.contains("adv-only") &&
    !!d.querySelector("style,link") );

  // adv mode shows
  d.getElementById("mode-adv").click();
  await tick(10);
  check("adv mode on", !d.body.classList.contains("simple"));
  d.getElementById("mode-simple").click();
  await tick(10);
  check("simple back on", d.body.classList.contains("simple"));

  // goal button sets percent
  const goals = d.querySelectorAll("#goalbtns button");
  goals[3].click();
  await tick(10);
  check("goal x2 sets 200", d.getElementById("percent").value === "200");

  // tool tab
  const tabs = d.querySelectorAll("#tablist .tab");
  tabs[1].click();
  await tick(20);
  check("toolview shown", !d.getElementById("toolview").hidden && d.getElementById("upscaleview").hidden);
  check("tool title", d.getElementById("tool-title").textContent === "Compressor");
  check("form fields", d.querySelectorAll("#tool-form select, #tool-form input").length === 3);

  // upload into tool
  const fi = d.getElementById("tool-file");
  Object.defineProperty(fi, "files", { value: [new window.File(["x".repeat(50)], "v.mp4", { type: "video/mp4" })], configurable: true });
  fi.dispatchEvent(new window.Event("change", { bubbles: true }));
  await tick(20);
  check("tool file shown", !d.getElementById("tool-fileinfo").hidden);
  check("tool meta table", d.getElementById("tool-meta").textContent.indexOf("640") >= 0);

  // start tool job
  calls.length = 0;
  d.getElementById("tool-run").click();
  await tick(30);
  check("media job posted", calls.some((c) => c === "POST /api/media/jobs"));
  check("tool progress", !d.getElementById("tool-progress").hidden);
  check("tool result", !d.getElementById("tool-result").hidden);
  const dl = d.querySelector('#tool-result a[href="/api/jobs/mj1/download"]');
  check("tool download link", !!dl);

  // back to upscale
  tabs[0].click();
  await tick(10);
  check("upscale back", !d.getElementById("upscaleview").hidden && d.getElementById("toolview").hidden);

  check("no window errors", errors.length === 0, errors.join("|"));
  console.log("FAIL count: " + fails.length);
  process.exit(fails.length ? 1 : 0);
})().catch((e) => { console.log("FAIL harness: " + (e && e.stack || e)); process.exit(1); });
