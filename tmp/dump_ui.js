// Печать того, что реально получается в боковой панели: группы, счётчики, вкладки.
const { JSDOM } = require("jsdom");
const path = require("path");
const fs = require("fs");

const root = path.join(__dirname, "..", "web");
const html = fs.readFileSync(path.join(root, "index.html"), "utf-8");
const appJs = fs.readFileSync(path.join(root, "app.js"), "utf-8");

const TABS = { groups: [
  { id: "video", title: "Видео" }, { id: "photo", title: "Фото" },
  { id: "audio", title: "Аудио" }, { id: "files", title: "Файлы" },
], tabs: [
  { id: "upscale", title: "Апскейл видео", ready: true, group: "video", hint: "нейросеть" },
  { id: "v-size", title: "Размер", ready: true, group: "video" },
  { id: "v-res", title: "Разрешение", ready: true, group: "video" },
  { id: "v-comp", title: "Компрессор", ready: true, group: "video" },
  { id: "v-speed", title: "Скорость", ready: true, group: "video" },
  { id: "v-cut", title: "Нарезка", ready: true, group: "video" },
  { id: "v-rotate", title: "Поворот", ready: true, group: "video" },
  { id: "v-audio", title: "Видео → аудио", ready: true, group: "video" },
  { id: "v-gif", title: "Видео → GIF", ready: true, group: "video" },
  { id: "v-mux", title: "Видео + аудио", ready: true, group: "video" },
  { id: "p-upscale", title: "Апскейл фото", ready: true, group: "photo", hint: "нейросеть" },
  { id: "p-size", title: "Размер", ready: true, group: "photo" },
  { id: "p-res", title: "Разрешение", ready: true, group: "photo" },
  { id: "p-comp", title: "Компрессор", ready: true, group: "photo" },
  { id: "a-comp", title: "Компрессор", ready: true, group: "audio" },
  { id: "a-volume", title: "Громкость", ready: true, group: "audio" },
  { id: "convert", title: "Конвертер", ready: true, group: "files", hint: "всё во всё" },
  { id: "meta", title: "Метаданные", ready: true, group: "files" },
] };
const HEALTH = { ok: true, ffmpeg_short: "2025-12-07", cores: 8, models_mb: 342, free_mb: 18000,
  devices: { gpus: [{ id: 0, name: "AMD Radeon RX 550X" }], gpu: { available: true, note: "" },
    cpus: { available: true } }, engines: {} };
const MODELS = { models: [{ id: "g", title: "general x4v3", summary: "s", scale: 4,
  engines: ["gpu"], size_mb: 2, default: true, installed: true }] };

const dom = new JSDOM(html, { url: "http://127.0.0.1:8756/", runScripts: "outside-only",
  pretendToBeVisual: true });
const { window } = dom;
const route = (u) => {
  if (u === "/api/tabs") return TABS;
  if (u === "/api/health") return HEALTH;
  if (u === "/api/settings") return { settings: {} };
  if (u === "/api/models") return MODELS;
  if (u === "/api/presets") return { built_in: [] };
  return {};
};
window.fetch = (u) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(route(String(u))) });
let tid = 1; const timers = new Map();
window.setTimeout = (fn) => { const id = tid++; timers.set(id, fn); return id; };
window.clearTimeout = (id) => timers.delete(id);

(async () => {
  window.eval(appJs);
  for (let i = 0; i < 40; i++) {
    await Promise.resolve();
    await new Promise((r) => setImmediate(r));
    [...timers.values()].forEach((fn) => fn());
    timers.clear();
  }
  const d = window.document;
  const line = (s) => console.log("  " + s);
  console.log("ШАПКА:");
  line(d.querySelector(".brand").textContent.replace(/\s+/g, " ") + "  [логотип " +
    d.querySelector(".brand img").getAttribute("src") + "]");
  console.log("РЕЖИМ/ТЕМА: " + [...d.querySelectorAll(".modeseg button")].map((b) =>
    (b.classList.contains("on") ? "*" : "") + b.textContent).join(" | "));
  console.log("БОКОВАЯ ПАНЕЛЬ (открыта одна группа):");
  d.querySelectorAll("#tablist .tgroup").forEach((g) => {
    const head = g.querySelector(".tgroup-head");
    line((g.classList.contains("closed") ? "  [ ] " : "  [+] ") + head.textContent);
    g.querySelectorAll(".tab").forEach((t) => {
      if (g.classList.contains("closed")) return;
      line("        " + (t.classList.contains("on") ? ">" : " ") + " " + t.textContent);
    });
  });
  console.log("ПАНЕЛЬ СОСТОЯНИЯ:");
  d.getElementById("health").querySelectorAll("div").forEach((x) => line(x.textContent));
  console.log("ФОРМА АПСКЕЙЛА ФОТО:");
  const tabs = d.querySelectorAll('#tablist .tgroup:not(.closed) .tab');
  const head = [...d.querySelectorAll("#tablist .tgroup-head")].find((h) => h.textContent.indexOf("Фото") >= 0);
  head.click();
  [...d.querySelectorAll('#tablist .tgroup:not(.closed) .tab')][0].click();
  for (let i = 0; i < 10; i++) await new Promise((r) => setImmediate(r));
  d.querySelectorAll("#tool-form > div").forEach((w) => {
    const inp = w.querySelector("input, select");
    const val = inp ? (inp.type === "checkbox" ? (inp.checked ? "да" : "нет") : inp.value) : "";
    line((w.hidden ? "(скрыто) " : "") + w.querySelector(".lbl").textContent + " = " + val);
  });
  process.exit(0);
})();
