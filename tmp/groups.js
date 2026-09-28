// Тесты свежего UI: темы, группы вкладок со счётчиками, апскейл фото нейросетью,
// превью результата и второй файл для «Видео + аудио».
const { JSDOM } = require("jsdom");
const path = require("path");
const fs = require("fs");

const root = path.join(__dirname, "..", "web");
const html = fs.readFileSync(path.join(root, "index.html"), "utf-8");
const appJs = fs.readFileSync(path.join(root, "app.js"), "utf-8");

const HEALTH = {
  ok: true, ffmpeg_short: "2025-12-07", cores: 8, models_mb: 342, free_mb: 18000,
  devices: { gpus: [{ id: 0, name: "RX 550X" }], gpu: { available: true, note: "" },
    cpus: { available: true } },
  engines: { gpu: { installed: true }, srmd: { installed: true }, cpu: { installed: true } },
};
const MODELS = { models: [
  { id: "general-x4", title: "Real-ESRGAN general", summary: "s", scale: 4,
    engines: ["gpu"], size_mb: 2, default: true, installed: true },
  { id: "srmd-x2", title: "SRMD", summary: "s", scale: 2,
    engines: ["srmd"], size_mb: 1, default: true, installed: true },
  { id: "cpu-x4", title: "realsr", summary: "s", scale: 4,
    engines: ["cpu"], size_mb: 2, default: true, installed: false },
] };
const TABS = { groups: [
  { id: "video", title: "Видео" },
  { id: "photo", title: "Фото" },
  { id: "audio", title: "Аудио" },
], tabs: [
  { id: "upscale", title: "Апскейл видео", ready: true, group: "video", hint: "нейросеть" },
  { id: "v-mux", title: "Видео + аудио", ready: true, group: "video" },
  { id: "p-upscale", title: "Апскейл фото", ready: true, group: "photo", hint: "нейросеть" },
  { id: "p-comp", title: "Компрессор", ready: true, group: "photo" },
  { id: "a-comp", title: "Компрессор", ready: true, group: "audio" },
] };
const SETTINGS = { settings: { engine: "gpu", model: "general-x4", scale_mode: "percent",
  percent: 200, width: 1920, height: 1080, long_edge: 1920, even_only: true } };
const MUX = { op: { id: "v-mux", title: "Видео + аудио", desc: "d", accept: "video/*",
  accept2: "audio/*", kinds: ["video"], submit: "Склеить", files: 2,
  fields: [{ key: "audio_bitrate", label: "Битрейт", type: "number", def: 192 }] } };
const PHOTO = { meta: { name: "p.jpg", size_bytes: 1000, width: 1920, height: 1080,
  video_codec: "mjpeg", kind: "image", token: "tokP",
  preview_url: "/api/media/file/tokP?inline=1" } };
const AUDIO = { meta: { name: "m.mp3", size_bytes: 500, duration: 90, audio_codec: "mp3",
  audio_channels: 2, audio_rate: 44100, kind: "audio", token: "tokA" } };
const VIDEO = { meta: { name: "v.mp4", size_bytes: 900, width: 640, height: 360, fps: 24,
  duration: 10, frames: 240, video_codec: "h264", has_audio: false, bitrate: 900,
  kind: "video", token: "tokV", preview_url: "/api/preview/tokV" } };
const PROBES = { image: PHOTO, audio: AUDIO, video: VIDEO };
let probeKind = "image";

let imgPolls = 0;
function imgupJob(stage) {
  return { job: { id: "mj2", stage, message: stage, percent: stage === "done" ? 100 : 40,
    frame: 0, frames_total: 100, speed: 0, eta: null, source: "p.jpg",
    output: stage === "done" ? "p_up2.png" : null, output_size: 2048,
    output_meta: stage === "done" ? { width: 3840, height: 2160, video_codec: "png" } : {},
    error: null, options: {} } };
}

const calls = [];
const errors = [];
function route(url, opts) {
  const m = (opts && opts.method) || "GET";
  calls.push(m + " " + String(url).split("?")[0]);
  if (url === "/api/tabs") return TABS;
  if (url === "/api/health") return HEALTH;
  if (url === "/api/settings") return SETTINGS;
  if (url === "/api/models") return MODELS;
  if (url === "/api/presets") return { built_in: [] };
  if (url === "/api/media/ops/v-mux") return MUX;
  if (url === "/api/media/probe") return PROBES[probeKind];
  if (url === "/api/imgup/jobs" && m === "POST") { imgPolls = 0; return imgupJob("upscaling"); }
  if (url === "/api/jobs/mj2") { imgPolls++; return imgPolls < 2 ? imgupJob("upscaling") : imgupJob("done"); }
  if (url.indexOf("/logs") >= 0) return { logs: [] };
  if (url.indexOf("/estimate") === 0) return { frames: 1, engine: "gpu", seconds: 1, out_mb: 1 };
  if (m === "PUT") return {};
  throw new Error("unexpected " + m + " " + url);
}

const dom = new JSDOM(html, { url: "http://127.0.0.1:8756/", runScripts: "outside-only",
  pretendToBeVisual: true });
const { window } = dom;
let lastBody = null;
window.fetch = (url, opts) => {
  lastBody = (opts && opts.body) || null;
  return Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve(route(String(url), opts || {})),
  });
};
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
const q = (sel) => window.document.querySelector(sel);
const qa = (sel) => [...window.document.querySelectorAll(sel)];
const groups = () => qa("#tablist .tgroup");
const groupOf = (name) => qa("#tablist .tgroup").find(
  (g) => g.querySelector(".tgroup-head").textContent.indexOf(name) >= 0);
const headOf = (name) => qa("#tablist .tgroup-head").find((h) => h.textContent.indexOf(name) >= 0);
const openCount = () => groups().filter((g) => !g.classList.contains("closed")).length;
// Вкладки конкретной группы по названию, независимо от того, раскрыта ли она.
const tabIn = (group, text) => [...groupOf(group).querySelectorAll(".tab")]
  .find((b) => b.textContent.indexOf(text) >= 0);

(async () => {
  const fails = [];
  const check = (n, c, x) => { console.log((c ? "PASS " : "FAIL ") + n + (x ? " — " + x : "")); if (!c) fails.push(n); };
  try { window.eval(appJs); } catch (e) { console.log("FAIL eval: " + e.message); process.exit(1); }
  const d = window.document;
  await tick(30);

  check("booted", window.__booted === true);

  // --- тема ---
  const rootEl = window.document.documentElement;
  check("тема задана", !!rootEl.getAttribute("data-theme"),
    rootEl.getAttribute("data-theme"));
  q("#theme-light").click();
  await tick(4);
  check("светлая включилась", rootEl.getAttribute("data-theme") === "light");
  check("светлая в localStorage", window.localStorage.getItem("pd_theme") === "light");
  check("кнопка подсвечена", q("#theme-light").classList.contains("on"));
  q("#theme-dark").click();
  await tick(4);
  check("тёмная вернулась", rootEl.getAttribute("data-theme") === "dark" &&
    window.localStorage.getItem("pd_theme") === "dark");

  // --- группы ---
  check("три группы", groups().length === 3, "групп: " + groups().length);
  check("счётчики в заголовках", headOf("Видео").textContent.indexOf("2") >= 0 &&
    headOf("Фото").textContent.indexOf("2") >= 0 && headOf("Аудио").textContent.indexOf("1") >= 0,
    headOf("Видео").textContent);
  check("изначально открыты все", openCount() === 3, "открыто: " + openCount());
  check("у открытой группы aria-expanded",
    groupOf("Видео").querySelector(".tgroup-head").getAttribute("aria-expanded") === "true");

  // Клик сворачивает только свою группу — остальные не трогаются.
  headOf("Фото").click();
  await tick(6);
  check("клик свернул «Фото»", groupOf("Фото").classList.contains("closed"));
  check("остальные группы не тронуты", openCount() === 2, "открыто: " + openCount());
  headOf("Фото").click();
  await tick(6);
  check("повторный клик раскрыл обратно", !groupOf("Фото").classList.contains("closed"));
  check("снова открыты все", openCount() === 3, "открыто: " + openCount());
  check("состояние папок сохранено",
    JSON.parse(window.localStorage.getItem("pd_groups") || "{}").photo === true);
  headOf("Аудио").click();
  await tick(6);
  check("две папки открыты одновременно",
    !groupOf("Фото").classList.contains("closed") && groupOf("Аудио").classList.contains("closed"),
    "открыто: " + openCount());
  headOf("Аудио").click();
  await tick(6);

  // --- апскейл фото нейросетью ---
  tabIn("Фото", "Апскейл фото").click();
  await tick(20);
  check("фото-вкладка не идёт в media/ops", !calls.includes("GET /api/media/ops/p-upscale"));
  check("заголовок вкладки", q("#tool-title").textContent === "Апскейл фото");
  check("форма фото собрана", !!q("#tf-engine") && !!q("#tf-model") && !!q("#tf-scale_mode"));
  check("модели по движку", q("#tf-model").value === "general-x4",
    q("#tf-model").value);
  q("#tf-engine").value = "srmd";
  q("#tf-engine").dispatchEvent(new window.Event("change", { bubbles: true }));
  await tick(6);
  check("смена движка меняет модели", q("#tf-model").value === "srmd-x2",
    q("#tf-model").value);
  q("#tf-engine").value = "cpu";
  q("#tf-engine").dispatchEvent(new window.Event("change", { bubbles: true }));
  await tick(6);
  check("процессор даёт свою модель", q("#tf-model").value === "cpu-x4");
  q("#tf-engine").value = "gpu";
  q("#tf-engine").dispatchEvent(new window.Event("change", { bubbles: true }));
  await tick(6);

  check("поля-условия скрыты", q("#tf-long_edge").parentNode.parentNode.hidden === true ||
    q("#tf-long_edge").closest("div").hidden === true);
  q("#tf-scale_mode").value = "long_edge";
  q("#tf-scale_mode").dispatchEvent(new window.Event("change", { bubbles: true }));
  await tick(6);
  check("«длинная сторона» появилась",
    q("#tf-long_edge").closest("div").hidden === false && q("#tf-percent").closest("div").hidden === true);
  q("#tf-scale_mode").value = "percent";
  q("#tf-scale_mode").dispatchEvent(new window.Event("change", { bubbles: true }));
  await tick(6);

  const fi = q("#tool-file");
  Object.defineProperty(fi, "files", { value: [new window.File(["x".repeat(100)], "p.jpg",
    { type: "image/jpeg" })], configurable: true });
  fi.dispatchEvent(new window.Event("change", { bubbles: true }));
  await tick(20);
  check("фото принято", !q("#tool-fileinfo").hidden);
  check("план увеличения", q("#tool-estimate").textContent.indexOf("3840") >= 0,
    q("#tool-estimate").textContent);

  calls.length = 0;
  q("#tool-run").click();
  await tick(30);
  check("запрос на /api/imgup/jobs", calls.some((c) => c === "POST /api/imgup/jobs"));
  check("прогресс показан", !q("#tool-progress").hidden);
  check("превью результата-картинки",
    !!q('#tool-result .resprev img[src="/api/jobs/mj2/download?inline=1"]'),
    q("#tool-result").innerHTML.slice(0, 120));
  check("размер результата в подписи",
    q("#tool-result .resprev-meta").textContent.indexOf("3840×2160") >= 0,
    q("#tool-result .resprev-meta").textContent);
  check("кнопка скачивания", !!q('#tool-result a[href="/api/jobs/mj2/download"]'));

  // --- второй файл для «Видео + аудио» ---
  tabIn("Видео", "Видео + аудио").click();
  await tick(20);
  check("вторая карточка показана", !q("#sec-tool2").hidden);
  check("второй accept — аудио", q("#tool-file2").getAttribute("accept") === "audio/*");
  const f2 = q("#tool-file2");
  probeKind = "audio";
  Object.defineProperty(f2, "files", { value: [new window.File(["y".repeat(50)], "m.mp3",
    { type: "audio/mpeg" })], configurable: true });
  f2.dispatchEvent(new window.Event("change", { bubbles: true }));
  await tick(20);
  check("второй файл принят", !q("#tool-fileinfo2").hidden);
  check("плеер второго файла", !!q("#tool-preview2 audio"));

  const f1 = q("#tool-file");
  probeKind = "video";
  Object.defineProperty(f1, "files", { value: [new window.File(["z".repeat(50)], "v.mp4",
    { type: "video/mp4" })], configurable: true });
  f1.dispatchEvent(new window.Event("change", { bubbles: true }));
  await tick(20);
  calls.length = 0;
  let posted = null;
  const prevFetch = window.fetch;
  window.fetch = (url, opts) => { if (String(url) === "/api/media/jobs") posted = JSON.parse(opts.body); return prevFetch(url, opts); };
  q("#tool-run").click();
  await tick(6);
  check("в media/jobs ушли оба токена",
    posted && posted.token === "tokV" && posted.token2 === "tokA", JSON.stringify(posted));

  // --- одиночная вкладка прячет вторую карточку ---
  tabIn("Аудио", "Компрессор").click();
  await tick(20);
  check("вторая карточка скрыта для одиночных инструментов", q("#sec-tool2").hidden === true);
  check("полоса нарезки спрятана вне нарезки", q("#sec-scrub").hidden === true);

  check("нет ошибок в консоли", errors.length === 0, errors.join("|"));
  console.log("FAIL count: " + fails.length);
  process.exit(fails.length ? 1 : 0);
})().catch((e) => { console.log("FAIL harness: " + (e && e.stack || e)); process.exit(1); });
