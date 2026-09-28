/* PedroDevTools — апскейл видео. Обычный скрипт без модулей: нечему ломаться при импорте. */
(function () {
"use strict";

var APP_VERSION = "3.2-groups-theme-imgup";

function $(id) { return document.getElementById(id); }
function on(el, ev, fn) { el.addEventListener(ev, fn); }
function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;"); }

function pad(n) { return (n < 10 ? "0" : "") + n; }
function stamp() {
  var d = new Date();
  return pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
}
function fmtBytes(n) {
  n = Number(n) || 0;
  if (n < 1024) return n + " Б";
  if (n < 1048576) return (n / 1024).toFixed(1) + " КБ";
  if (n < 1073741824) return (n / 1048576).toFixed(1) + " МБ";
  return (n / 1073741824).toFixed(2) + " ГБ";
}
function fmtDur(s) {
  s = Math.max(0, Math.floor(Number(s) || 0));
  var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return pad(h) + ":" + pad(m) + ":" + pad(s % 60);
}
function fmtBitrate(bps) {
  var n = Number(bps) || 0;
  if (!n) return "—";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + " Мбит/с";
  return Math.round(n / 1e3) + " кбит/с";
}
function fmtLeft(s) {
  s = Math.max(0, Math.round(Number(s) || 0));
  if (!s) return "—";
  var m = Math.floor(s / 60);
  if (m > 0) return m + " мин " + pad(s % 60) + " с";
  return s + " с";
}
function even(n) { n = Math.max(2, Math.round(Number(n) || 2)); return n - (n % 2); }
function clamp(n, a, b) { return Math.min(b, Math.max(a, n)); }

var logBox = $("log");
var logLines = [];
function log(text, cls) {
  logLines.push({ t: stamp(), text: String(text), cls: cls || "" });
  if (logLines.length > 150) logLines.splice(0, logLines.length - 150);
  var html = "";
  for (var i = 0; i < logLines.length; i++) {
    var L = logLines[i];
    html += '<div><span class="t">' + L.t + '</span> <span class="' + L.cls + '">' + esc(L.text) + "</span></div>";
  }
  logBox.innerHTML = html;
  logBox.scrollTop = logBox.scrollHeight;
}

function toast(text, isErr) {
  var box = $("toast");
  var d = document.createElement("div");
  if (isErr) d.className = "err";
  d.textContent = text;
  box.appendChild(d);
  setTimeout(function () { d.remove(); }, isErr ? 8000 : 4000);
}

function errBox(text) {
  var box = $("err");
  box.hidden = false;
  box.textContent = text;
}

function get(url) {
  return fetch(url).then(function (r) {
    return r.json().then(function (body) {
      if (!r.ok) throw new Error((body && (body.error || body.detail)) || ("HTTP " + r.status));
      return body;
    });
  });
}
function post(url, data) {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data || {})
  }).then(function (r) {
    return r.json().then(function (body) {
      if (!r.ok) throw new Error((body && (body.error || body.detail)) || ("HTTP " + r.status));
      return body;
    });
  });
}
function put(url, data) {
  return fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data || {})
  }).then(function (r) { return r.json(); });
}

var S = {
  health: null,
  models: [],
  presets: [],
  settings: {},
  meta: null,
  token: null,
  job: null,
  timer: null,
  logTimer: null,
  logSeen: 0,
  installing: false,
  wantStart: false,
  tab: "upscale",
  tabs: [],
  groups: [],
  openGroups: {}
};

/* Строка-подсказка под кнопкой запуска. Сами кнопки НЕ блокируются никогда:
   клик всегда отрабатывает, а если запускать рано — говорит, чего не хватает.
   Так убит целый класс багов «кнопка серая и молчит». */
function paintWhy() {
  var why = $("start-why");
  if (!why) return;
  var reason = "";
  if (!S.meta) {
    reason = "Загрузите видео.";
  } else if (busy()) {
    reason = "";
  } else {
    var m = curModel();
    if (!m) {
      reason = "Нет модели для этого движка.";
    } else if (!m.installed) {
      reason = S.installing
        ? "Качаю «" + m.title + "» — запустится само, ждите."
        : "Модель докачается при запуске — жмите смело.";
    }
  }
  why.textContent = reason;
}

function rpc(name, value) {
  var list = document.getElementsByName(name);
  for (var i = 0; i < list.length; i++) {
    if (list[i].value === String(value)) list[i].checked = true;
  }
}
function rval(name) {
  var list = document.getElementsByName(name);
  for (var i = 0; i < list.length; i++) {
    if (list[i].checked) return list[i].value;
  }
  return "";
}

/* ---------- режим простой/расширенный ---------- */

function isSimple() {
  return document.body.classList.contains("simple");
}

function setMode(mode, save) {
  var simple = mode !== "adv";
  document.body.classList.toggle("simple", simple);
  $("mode-simple").classList.toggle("on", simple);
  $("mode-adv").classList.toggle("on", !simple);
  if (save !== false) {
    try { localStorage.setItem("pd_mode", simple ? "simple" : "adv"); } catch (e) {}
  }
  syncPanels();
  recalc();
}

function initMode() {
  var saved = null;
  try { saved = localStorage.getItem("pd_mode"); } catch (e) {}
  setMode(saved === "adv" ? "adv" : "simple", false);
  on($("mode-simple"), "click", function () { setMode("simple"); });
  on($("mode-adv"), "click", function () { setMode("adv"); });
}

/* ---------- тема: тёмная / светлая ---------- */

function setTheme(name, save) {
  var t = name === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", t);
  document.body.setAttribute("data-theme", t);
  if ($("theme-dark")) $("theme-dark").classList.toggle("on", t === "dark");
  if ($("theme-light")) $("theme-light").classList.toggle("on", t === "light");
  if (save !== false) {
    try { localStorage.setItem("pd_theme", t); } catch (e) {}
  }
}

function initTheme() {
  var saved = null;
  try { saved = localStorage.getItem("pd_theme"); } catch (e) {}
  if (saved !== "light" && saved !== "dark") saved = "dark";
  setTheme(saved, false);
  on($("theme-dark"), "click", function () { setTheme("dark"); });
  on($("theme-light"), "click", function () { setTheme("light"); });
}

var GOALS = [
  { t: "720p", mode: "long_edge", edge: 1280 },
  { t: "1080p", mode: "long_edge", edge: 1920 },
  { t: "4K", mode: "long_edge", edge: 3840 },
  { t: "×2", mode: "percent", percent: 200 },
  { t: "×4", mode: "percent", percent: 400 }
];

function renderGoals() {
  var host = $("goalbtns");
  if (!host) return;
  host.innerHTML = "";
  GOALS.forEach(function (g) {
    var b = document.createElement("button");
    b.type = "button";
    b.textContent = g.t;
    on(b, "click", function () {
      rpc("scale_mode", g.mode);
      if (g.mode === "percent") {
        $("percent").value = g.percent;
      } else {
        $("long_edge").value = g.edge;
      }
      syncPairs();
      syncPanels();
      changed();
      log("Цель: " + g.t, "ok");
    });
    host.appendChild(b);
  });
}

/* ---------- вкладки: группы со счётчиками ---------- */

/* Группы приходят с сервера (/api/tabs): у каждой вкладки есть поле group.
   Заголовок показывает, сколько вкладок в папке, и раскрывает только её.
   Группы независимы: хочется держать открытыми сразу несколько папок,
   поэтому клик по одному заголовку не трогает остальные. */
function renderTabs(tabs, groups) {
  S.tabs = tabs || [];
  if (groups) S.groups = groups;
  var host = $("tablist");
  host.innerHTML = "";
  var buckets = [], index = {};
  (S.groups || []).forEach(function (g) {
    var b = { id: g.id, title: g.title, items: [] };
    index[g.id] = b;
    buckets.push(b);
  });
  S.tabs.forEach(function (t) {
    var gid = t.group || "";
    if (!index[gid]) {
      var b2 = { id: gid, title: gid || "Прочее", items: [] };
      index[gid] = b2;
      buckets.push(b2);
    }
    index[gid].items.push(t);
  });

  buckets.forEach(function (b) {
    if (!b.items.length) return;
    // Не задан явно — считаем открытой, чтобы список был виден целиком.
    var open = S.openGroups[b.id] !== false;
    var wrap = document.createElement("div");
    wrap.className = "tgroup" + (open ? "" : " closed");
    var head = document.createElement("button");
    head.type = "button";
    head.className = "tgroup-head" + (open ? " on" : "");
    head.setAttribute("aria-expanded", open ? "true" : "false");
    head.innerHTML = '<span class="caret">▾</span><span class="gtitle">' + esc(b.title) +
      '</span><span class="cnt">' + b.items.length + "</span>";
    on(head, "click", function () {
      S.openGroups[b.id] = !open;
      saveOpenGroups();
      renderTabs(S.tabs, S.groups);
    });
    wrap.appendChild(head);

    var body = document.createElement("div");
    body.className = "tgroup-body";
    b.items.forEach(function (t) {
      var active = t.id === S.tab;
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "tab" + (t.ready ? (active ? " on" : "") : " lock");
      btn.innerHTML = esc(t.title) +
        (t.ready ? "" : "<small>скоро</small>") +
        (t.hint ? '<small class="hint">' + esc(t.hint) + "</small>" : "");
      if (!t.ready) {
        on(btn, "click", function () { toast(t.title + " — в разработке"); });
      } else if (t.id === "upscale") {
        on(btn, "click", function () { showUpscale(); });
      } else {
        on(btn, "click", function () { showTool(t.id); });
      }
      body.appendChild(btn);
    });
    wrap.appendChild(body);
    host.appendChild(wrap);
  });
}

function saveOpenGroups() {
  try { localStorage.setItem("pd_groups", JSON.stringify(S.openGroups)); } catch (e) {}
}

/* Активная вкладка всегда должна быть видна — её группу раскрываем. */
function openGroupFor(tabId) {
  var t = (S.tabs || []).filter(function (x) { return x.id === tabId; })[0];
  if (t && t.group) S.openGroups[t.group] = true;
}

function dot(ok) { return '<span class="dot ' + (ok ? "ok" : "bad") + '"></span>'; }

function renderHealth(h) {
  var g = (h.devices && h.devices.gpus && h.devices.gpus[0]) || null;
  var html = "";
  html += "<div>" + dot(h.ok) + "ffmpeg <b>" + esc(h.ffmpeg_short || "?") + "</b></div>";
  html += "<div>" + dot(h.devices.gpu.available) + "GPU: <b>" + esc(g ? g.name : "нет") + "</b></div>";
  html += "<div>" + dot(h.devices.cpus.available) + "CPU: <b>" + h.cores + " потоков</b></div>";
  html += "<div>Модели: <b>" + h.models_mb + " МБ</b></div>";
  html += "<div>Диск: <b>" + h.free_mb + " МБ</b></div>";
  $("health").innerHTML = html;
}

/* ---------- файл ---------- */

function setupFile() {
  var drop = $("drop"), input = $("file");
  on(drop, "click", function () { input.click(); });
  on(input, "change", function () { if (input.files[0]) upload(input.files[0]); });
  ["dragenter", "dragover"].forEach(function (ev) {
    on(drop, ev, function (e) { e.preventDefault(); drop.classList.add("over"); });
  });
  ["dragleave", "drop"].forEach(function (ev) {
    on(drop, ev, function (e) { e.preventDefault(); drop.classList.remove("over"); });
  });
  on(drop, "drop", function (e) {
    var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) upload(f);
  });
}

function upload(file) {
  if (!/^video\//.test(file.type) && !/\.(mp4|mkv|webm|mov|avi|m4v)$/i.test(file.name)) {
    toast("Это не видеофайл", true);
    return;
  }
  log("Загрузка " + file.name + " (" + fmtBytes(file.size) + ")…");
  var fd = new FormData();
  fd.append("file", file, file.name);
  fetch("/api/probe", { method: "POST", body: fd }).then(function (r) {
    return r.json().then(function (body) {
      if (!r.ok) throw new Error(body.error || ("HTTP " + r.status));
      return body;
    });
  }).then(function (data) {
    S.meta = data.meta;
    S.token = data.meta.token;
    renderFile();
    // Только стартовый процент из подсказки сервера — движок и модель
    // пользователя НЕ трогаем (раньше applySettings сносил их к загрузочным).
    if (data.meta.suggested && data.meta.suggested.factor) {
      $("percent").value = Math.round(data.meta.suggested.factor * 100);
      syncPairs();
    }
    recalc();
    log("Файл разобран: " + S.meta.width + "×" + S.meta.height + ", " + S.meta.fps + " fps, " + fmtDur(S.meta.duration), "ok");
  }).catch(function (e) {
    log("Ошибка разбора: " + e.message, "error");
    toast(e.message, true);
  });
}

function renderFile() {
  var m = S.meta;
  var fi = $("fileinfo");
  fi.hidden = false;
  fi.textContent = m.name + " · " + fmtBytes(m.size_bytes);
  var rows = [
    ["Разрешение", m.width + "×" + m.height],
    ["Кадры", m.fps + " fps · " + m.frames + " кадров"],
    ["Длительность", fmtDur(m.duration)],
    ["Видео", m.video_codec],
    ["Аудио", m.has_audio ? (m.audio_codec + " · " + m.audio_channels + " кан.") : "нет"],
    ["Битрейт", m.bitrate > 1e6 ? (m.bitrate / 1e6).toFixed(1) + " Мбит/с" : Math.round(m.bitrate / 1e3) + " кбит/с"]
  ];
  var html = "";
  rows.forEach(function (r) { html += "<tr><td>" + r[0] + "</td><td>" + esc(String(r[1])) + "</td></tr>"; });
  var mt = $("metatable");
  mt.hidden = false;
  mt.innerHTML = html;
  var pv = $("preview");
  if (m.source_url) {
    // Показываем само видео с плеером: одного первого кадра мало, чтобы
    // понять, что загрузилось. Источник отдаётся inline — иначе браузер
    // не играет, а скачивает файл.
    pv.hidden = false;
    pv.innerHTML = '<video controls preload="metadata" playsinline src="' +
      m.source_url + '"></video>';
  } else if (m.preview_url) {
    pv.hidden = false;
    pv.innerHTML = '<img src="' + m.preview_url + '" alt="первый кадр">';
  }
  paintWhy();
}

/* ---------- настройки ---------- */

var FIELDS = ["percent", "width", "height", "long_edge", "crf", "bitrate", "fps",
  "audio_bitrate", "time_start", "duration", "threads", "tile", "chunk_seconds",
  "container", "codec", "x264_preset", "audio"];

function readUI() {
  var o = {};
  // В простом режиме движок всегда авто: GPU если есть, иначе процессор.
  o.engine = isSimple() ? "auto" : (rval("engine") || "gpu");
  o.model = $("model").value;
  o.scale_mode = rval("scale_mode") || "percent";
  FIELDS.forEach(function (k) {
    var el = $(k);
    if (el) o[k] = el.value;
  });
  o.percent = Number($("percent").value) || 200;
  o.width = even($("width").value);
  o.height = even($("height").value);
  o.long_edge = Number($("long_edge").value) || 1920;
  o.crf = clamp(Number($("crf").value) || 17, 0, 51);
  o.bitrate = Number($("bitrate").value) || 8000;
  o.crf_mode = rval("crf_mode") || "crf";
  o.fps = Number($("fps").value) || 0;
  o.tile = Number($("tile").value) || 0;
  o.threads = Number($("threads").value) || 1;
  o.tta = $("tta").checked;
  o.chunk_seconds = Number($("chunk_seconds").value) || 10;
  o.keep_width = $("keep_width").checked;
  o.keep_height = $("keep_height").checked;
  o.even_only = $("even_only").checked;
  o.start = Number($("time_start").value) || 0;
  o.duration = Number($("duration").value) || 0;
  return o;
}

function applySettings(s) {
  S.settings = s;
  rpc("engine", s.engine || "gpu");
  rpc("scale_mode", s.scale_mode || "percent");
  rpc("crf_mode", s.crf_mode || "crf");
  if ($("model") && s.model) $("model").value = s.model;
  FIELDS.forEach(function (k) {
    if ($(k) && s[k] !== undefined) $(k).value = s[k];
  });
  $("tta").checked = !!s.tta;
  $("keep_width").checked = !!s.keep_width;
  $("keep_height").checked = s.keep_height !== false;
  $("even_only").checked = s.even_only !== false;
  syncPairs();
  syncPanels();
}

var saveTimer = null;
function saveSoon() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(function () {
    put("/api/settings", { settings: readUI() }).catch(function () {});
  }, 600);
}

function syncPairs() {
  $("percent-n").value = $("percent").value;
  $("long_edge-n").value = $("long_edge").value;
  $("crf-n").value = $("crf").value;
}
function syncPanels() {
  var mode = rval("scale_mode");
  $("p-percent").hidden = mode !== "percent";
  $("p-pixels").hidden = mode !== "pixels";
  $("p-edge").hidden = mode !== "long_edge";
  var cm = rval("crf_mode");
  $("w-crf").hidden = cm !== "crf";
  $("w-bitrate").hidden = cm !== "bitrate";
}

function setupInputs() {
  var pairs = [["percent", "percent-n"], ["long_edge", "long_edge-n"], ["crf", "crf-n"]];
  pairs.forEach(function (p) {
    var r = $(p[0]), n = $(p[1]);
    on(r, "input", function () { n.value = r.value; changed(); });
    on(n, "change", function () { r.value = n.value; changed(); });
  });
  var all = document.querySelectorAll("input, select");
  for (var i = 0; i < all.length; i++) {
    (function (el) {
      if (el.id === "file" || el.id === "preset") return;
      var ev = (el.type === "range") ? "input" : "change";
      on(el, ev, changed);
    })(all[i]);
  }
  var radios = document.querySelectorAll('input[name="scale_mode"], input[name="crf_mode"], input[name="engine"]');
  for (var j = 0; j < radios.length; j++) {
    (function (el) { on(el, "change", function () { syncPanels(); changed(); }); })(radios[j]);
  }
  var quick = [[150, "150%"], [200, "200%"], [300, "300%"], [400, "400%"]];
  var pq = $("pct-quick");
  quick.forEach(function (q) {
    var b = document.createElement("button");
    b.textContent = q[1];
    on(b, "click", function () { $("percent").value = q[0]; syncPairs(); changed(); });
    pq.appendChild(b);
  });
  var res = [["720p", 1280, 720], ["1080p", 1920, 1080], ["1440p", 2560, 1440], ["4K", 3840, 2160]];
  var rq = $("res-quick");
  res.forEach(function (r) {
    var b = document.createElement("button");
    b.textContent = r[0];
    on(b, "click", function () {
      $("width").value = r[1]; $("height").value = r[2];
      $("keep_width").checked = false; $("keep_height").checked = false;
      changed();
    });
    rq.appendChild(b);
  });
  on($("model"), "change", function () { changed(); ensureModel(); });
  on($("preset"), "change", applyPreset);
}

var estTimer = null;
function changed() {
  syncPairs();
  saveSoon();
  renderModels();
  recalc();
  clearTimeout(estTimer);
  estTimer = setTimeout(estimate, 500);
}

/* ---------- модели ---------- */

function engineModels(engine) {
  return S.models.filter(function (m) { return m.engines.indexOf(engine) >= 0; });
}
/* Движок для фильтрации моделей в UI. В простом режиме радиокнопки скрыты
   и readUI шлёт "auto" — по строке "auto" моделей нет, поэтому разрешаем
   здесь же: GPU если есть, иначе процессор (как бэкенд в resolve_engine). */
function uiEngine() {
  if (!isSimple()) return rval("engine") || "gpu";
  var h = S.health;
  if (h && h.devices && h.devices.gpu && h.devices.gpu.available) return "gpu";
  return "cpu";
}
function curModel() {
  var list = engineModels(uiEngine());
  var id = $("model").value;
  for (var i = 0; i < list.length; i++) {
    if (list[i].id === id) return list[i];
  }
  for (var j = 0; j < list.length; j++) {
    if (list[j].default) return list[j];
  }
  return list[0] || null;
}

function renderModels() {
  var engine = uiEngine();
  var list = engineModels(engine);
  var sel = $("model");
  var cur = sel.value;
  sel.innerHTML = "";
  list.forEach(function (m) {
    var o = document.createElement("option");
    o.value = m.id;
    o.textContent = m.title + " ×" + m.scale + (m.installed ? "" : " (скачать " + m.size_mb + " МБ)");
    sel.appendChild(o);
  });
  var ok = list.some(function (m) { return m.id === cur; });
  sel.value = ok ? cur : (list[0] && list[0].id) || "";
  var m = curModel();
  $("model-desc").textContent = m ? m.summary : "";
  renderEngineNote();
}

function engineLabel(e) {
  if (e === "gpu") return "GPU";
  if (e === "cpu") return "CPU";
  if (e === "srmd") return "SRMD";
  return e;
}

function srmdReady(h) {
  return !!(h.devices.gpu.available && h.engines && h.engines.srmd && h.engines.srmd.installed);
}

function renderEngineNote() {
  var h = S.health, note = $("engine-note");
  var engine = readUI().engine;
  if (!h) { note.hidden = true; return; }
  if (engine === "gpu" && !h.devices.gpu.available) {
    note.hidden = false;
    note.className = "note bad";
    note.textContent = "GPU недоступен: " + (h.devices.gpu.note || "нет Vulkan") + ". Выберите процессор.";
  } else if (engine === "cpu") {
    note.hidden = false;
    note.className = "note warn";
    note.textContent = "На процессоре в десятки раз медленнее. Минута 1080p — это часы.";
  } else if (engine === "srmd") {
    if (!srmdReady(h)) {
      note.hidden = false;
      note.className = "note bad";
      note.textContent = "SRMD недоступен: нужен Vulkan и установленный движок srmd-ncnn-vulkan.";
    } else {
      note.hidden = false;
      note.className = "note ok";
      note.textContent = "SRMD: нативный ×2/×3/×4 для людей, в разы быстрее Real-ESRGAN, чуть мягче детали.";
    }
  } else {
    note.hidden = true;
  }
  var gr = document.querySelector('input[name="engine"][value="gpu"]');
  if (gr) gr.disabled = !h.devices.gpu.available;
  var sr = document.querySelector('input[name="engine"][value="srmd"]');
  if (sr) sr.disabled = !srmdReady(h);
  var cr = document.querySelector('input[name="engine"][value="cpu"]');
  if (cr) cr.disabled = !h.devices.cpus.available;
  var gn = $("gpu-name");
  if (gn && h.devices.gpus[0]) gn.textContent = "· " + h.devices.gpus[0].name;
}

function ensureModel() {
  var m = curModel();
  var st = $("model-status");
  if (!m) { st.hidden = true; paintWhy(); return; }
  if (m.installed) { st.hidden = true; paintWhy(); return; }
  st.hidden = false;
  st.className = "note warn";
  st.textContent = "Скачиваю «" + m.title + "» (" + m.size_mb + " МБ)…";
  paintWhy();
  if (S.installing) return;
  S.installing = true;
  paintWhy();
  log("Скачивание модели «" + m.title + "»…");
  post("/api/models/" + m.id + "/install").then(function () {
    return refreshModels();
  }).then(function () {
    S.installing = false;
    renderModels();
    var fresh = curModel();
    if (fresh && fresh.installed) {
      st.hidden = true;
      log("Модель «" + fresh.title + "» готова", "ok");
      paintWhy();
      if (S.wantStart && S.meta && !busy()) {
        S.wantStart = false;
        log("Модель докачалась — запускаю сам", "ok");
        start();
      }
    } else {
      st.className = "note bad";
      st.textContent = "Скачивание завершилось, но файлы модели не прошли проверку.";
      log("Модель не прошла проверку файлов", "error");
      paintWhy();
    }
  }).catch(function (e) {
    S.installing = false;
    st.className = "note bad";
    st.textContent = "Не скачалась: " + e.message + " — нажмите запуск ещё раз, попробую снова.";
    log("Модель не скачалась: " + e.message, "error");
    paintWhy();
  });
}

function refreshModels() {
  return get("/api/models").then(function (d) {
    S.models = d.models || [];
    return S.models;
  });
}

/* ---------- расчёт ---------- */

function modelScales(m) {
  if (!m) return [4];
  var s = Number(m.scale) || 4;
  if (m.engines[0] === "cpu") return [4];
  if (s === 2 || s === 3 || s === 4) return [s];
  return [4];
}

function computeTarget() {
  if (!S.meta) return null;
  var o = readUI();
  var sw = S.meta.width, sh = S.meta.height, tw, th;
  if (o.scale_mode === "percent") {
    var f = (Number(o.percent) || 200) / 100;
    tw = sw * f; th = sh * f;
  } else if (o.scale_mode === "long_edge") {
    var e = Number(o.long_edge) || 1920;
    if (sw >= sh) { tw = e; th = e * sh / sw; } else { th = e; tw = e * sw / sh; }
  } else {
    var w = Number(o.width) || sw, h = Number(o.height) || sh;
    if (o.keep_width && !o.keep_height) { tw = w; th = w * sh / sw; }
    else if (o.keep_height && !o.keep_width) { th = h; tw = h * sw / sh; }
    else { tw = w; th = h; }
  }
  tw = Math.max(2, Math.round(tw));
  th = Math.max(2, Math.round(th));
  if (o.even_only) { tw = even(tw); th = even(th); }
  var factor = Math.max(1, tw / sw, th / sh);
  var scales = modelScales(curModel()).sort(function (a, b) { return b - a; });
  var ms = scales[scales.length - 1];
  for (var i = 0; i < scales.length; i++) {
    if (factor >= scales[i] - 1e-6) { ms = scales[i]; break; }
  }
  return { w: tw, h: th, factor: factor, ms: ms, resize: (sw * ms !== tw || sh * ms !== th) };
}

function recalc() {
  var box = $("calc"), note = $("scale-note");
  var t = computeTarget();
  if (!t) {
    box.innerHTML = "Загрузите видео — здесь появится расчёт.";
    note.hidden = true;
    return;
  }
  box.innerHTML = '<span class="from">' + S.meta.width + "×" + S.meta.height + '</span>' +
    '<span class="x">→</span>' + '<span class="to">' + t.w + "×" + t.h + "</span>" +
    '<span class="x">×' + (Math.round(t.w / S.meta.width * 100) / 100) + "</span>";
  if (t.resize) {
    note.hidden = false;
    note.className = "note warn";
    note.textContent = "Модель увеличивает только ×" + t.ms +
      ", до " + t.w + "×" + t.h + " дотянет ресайз Lanczos — это точнее по размеру, но не нейросеть.";
  } else {
    note.hidden = false;
    note.className = "note ok";
    note.textContent = "Модель ×" + t.ms + " попадает точно в запрос.";
  }
}

function estimate() {
  if (!S.token) return;
  var q = "/api/estimate?token=" + S.token + "&options=" + encodeURIComponent(JSON.stringify(readUI()));
  get(q).then(function (d) {
    $("estimate").textContent = "Кадров: " + d.frames + " · движок: " +
      engineLabel(d.engine) + " · примерно: " + fmtLeft(d.seconds) +
      " · файл: ~" + fmtBytes(d.out_mb * 1048576);
  }).catch(function () {});
}

/* ---------- пресеты ---------- */

function renderPresets(list) {
  var sel = $("preset");
  sel.innerHTML = "";
  var o0 = document.createElement("option");
  o0.value = "";
  o0.textContent = "— выбрать пресет —";
  sel.appendChild(o0);
  (list || []).forEach(function (p) {
    var o = document.createElement("option");
    o.value = p.id;
    o.textContent = p.title;
    o._values = p.values;
    sel.appendChild(o);
  });
}
function applyPreset() {
  var sel = $("preset");
  var opt = sel.options[sel.selectedIndex];
  if (!opt || !opt._values) return;
  var v = opt._values;
  Object.keys(v).forEach(function (k) {
    var el = $(k);
    if (!el) return;
    if (el.type === "checkbox") el.checked = !!v[k];
    else if (el.type === "radio") rpc(el.name, v[k]);
    else el.value = v[k];
  });
  if (v.scale_mode) rpc("scale_mode", v.scale_mode);
  if (v.crf_mode) rpc("crf_mode", v.crf_mode);
  if (v.engine) rpc("engine", v.engine);
  syncPairs();
  syncPanels();
  changed();
  log("Пресет применён", "ok");
}

/* ---------- запуск ---------- */

function busy() {
  return S.job && ["queued", "probing", "preparing", "decoding", "upscaling", "encoding", "assembling"].indexOf(S.job.stage) >= 0;
}

function start(e) {
  // Один физический клик доходит трижды (прямой хендлер + onclick + делегирование)
  // ОДНИМ И ТЕМ ЖЕ объектом события — повтор по нему игнорируем.
  // Вызовы без события (автозапуск после докачки) идут всегда.
  if (e && e.__pedroHandled) return;
  if (e) { try { e.__pedroHandled = true; } catch (err) {} }
  if (!S.token || !S.meta) {
    toast("Сначала загрузите видео (раздел 1)", true);
    log("Запуск отклонён: нет файла", "warn");
    paintWhy();
    return;
  }
  if (busy()) {
    toast("Задача уже идёт — дождитесь конца или нажмите «Отменить»", true);
    return;
  }
  var m = curModel();
  if (!m) {
    toast("Нет модели для этого движка", true);
    log("Запуск отклонён: нет модели", "warn");
    return;
  }
  if (!m.installed) {
    S.wantStart = true;
    ensureModel();
    toast("Докачаю «" + m.title + "» и запущу сам");
    return;
  }
  var o = readUI();
  var t = computeTarget();
  log("Старт: " + (m ? m.title : "?") + ", " + S.meta.width + "×" + S.meta.height +
    " → " + t.w + "×" + t.h + ", " + engineLabel(o.engine === "auto" ? uiEngine() : o.engine) + ", " + o.codec);
  post("/api/jobs", { token: S.token, options: o }).then(function (d) {
    S.job = d.job;
    paintJob(S.job);
    poll();
  }).catch(function (e) {
    log("Не запустилось: " + e.message, "error");
    toast(e.message, true);
  });
}

function cancel(e) {
  if (e && e.__pedroHandled) return;
  if (e) { try { e.__pedroHandled = true; } catch (err) {} }
  if (!S.job || !busy()) {
    toast("Нет активной задачи — отменять нечего");
    return;
  }
  post("/api/jobs/" + S.job.id + "/cancel").then(function () {
    toast("Отменяю…");
  }).catch(function (e) { toast(e.message, true); });
}

function poll() {
  clearTimeout(S.timer);
  if (!S.job) return;
  S.timer = setTimeout(function () {
    get("/api/jobs/" + S.job.id).then(function (d) {
      S.job = d.job;
      paintJob(S.job);
      tailLogs();
      if (busy()) poll();
      else finished(S.job);
    }).catch(function () { if (busy()) poll(); });
  }, 800);
}

function tailLogs() {
  if (!S.job) return;
  get("/api/jobs/" + S.job.id + "/logs?since=" + S.logSeen).then(function (d) {
    (d.logs || []).forEach(function (e) { log(e.text, e.level); });
    S.logSeen += (d.logs || []).length;
  }).catch(function () {});
}

function paintJob(j) {
  paintWhy();
  var p = $("progress");
  p.hidden = false;
  $("p-stage").textContent = j.message || j.stage;
  $("p-pct").textContent = Math.round(j.percent) + "%";
  $("p-fill").style.width = j.percent + "%";
  var stats = "кадр " + j.frame + " / " + j.frames_total;
  if (j.speed) stats += " · " + j.speed + " кадров/с";
  if (j.eta) stats += " · осталось " + fmtLeft(j.eta);
  $("p-stats").textContent = stats;
}

/* ---------- результат: превью + скачивание ---------- */

var IMAGE_EXT = /\.(jpg|jpeg|png|webp|gif|bmp|tiff?)$/i;
var AUDIO_EXT = /\.(mp3|m4a|ogg|oga|opus|wav|flac|aac|wma)$/i;
var VIDEO_EXT = /\.(mp4|mkv|webm|mov|avi|m4v)$/i;

function resultKind(name) {
  if (IMAGE_EXT.test(name)) return "image";
  if (AUDIO_EXT.test(name)) return "audio";
  if (VIDEO_EXT.test(name)) return "video";
  return "";
}

function paintResult(box, j) {
  box.innerHTML = "";
  var name = document.createElement("span");
  name.className = "name";
  name.textContent = j.output + " · " + fmtBytes(j.output_size);
  var a = document.createElement("a");
  a.className = "btn primary";
  a.textContent = "Скачать";
  a.href = "/api/jobs/" + j.id + "/download";
  a.setAttribute("download", "");
  box.appendChild(name);
  box.appendChild(a);

  var url = "/api/jobs/" + encodeURIComponent(j.id) + "/download?inline=1";
  var kind = resultKind(j.output || "");
  var prev = document.createElement("div");
  prev.className = "resprev";
  var cap = '<div class="resprev-cap">Результат</div>';
  if (kind === "image") {
    prev.innerHTML = cap + '<img src="' + url + '" alt="результат">';
  } else if (kind === "audio") {
    prev.innerHTML = cap + '<audio controls preload="metadata" src="' + url + '"></audio>';
  } else if (kind === "video") {
    prev.innerHTML = cap + '<video controls preload="metadata" playsinline src="' + url + '"></video>';
  } else {
    prev.innerHTML = cap + '<div class="resprev-meta">Браузер этот формат не показывает — файл можно скачать.</div>';
  }

  var meta = j.output_meta || {};
  var bits = [];
  if (meta.width && meta.height) bits.push(meta.width + "×" + meta.height);
  if (meta.duration) bits.push(fmtDur(meta.duration));
  if (meta.video_codec) bits.push(meta.video_codec);
  if (meta.audio_codec) bits.push(meta.audio_codec);
  if (meta.bitrate) bits.push(fmtBitrate(meta.bitrate));
  if (/\.(mkv|avi)$/i.test(j.output || "")) {
    bits.push("браузер MKV/AVI не играет — откройте в плеере");
  }
  if (bits.length) {
    var m = document.createElement("div");
    m.className = "resprev-meta";
    m.textContent = bits.join(" · ");
    prev.appendChild(m);
  }
  box.appendChild(prev);
}

function finished(j) {
  tailLogs();
  var r = $("result");
  if (j.stage === "done" && j.output) {
    r.hidden = false;
    paintResult(r, j);
    log("Готово: " + j.output, "ok");
    toast("Готово: " + j.output);
  } else if (j.stage === "failed") {
    r.hidden = false;
    r.innerHTML = '<span style="color:var(--bad)">Ошибка: ' + esc(j.error || "?") + "</span>";
    log("Ошибка: " + (j.error || "?"), "error");
  } else {
    log("Задача отменена", "warn");
  }
  paintWhy();
}

function copyCmd() {
  if (!S.token) { toast("Сначала загрузите видео"); return; }
  var q = "/api/command?token=" + S.token + "&options=" + encodeURIComponent(JSON.stringify(readUI()));
  get(q).then(function (d) {
    var txt = "ffmpeg:\n" + d.command + "\n\nапскейлер:\n" + d.upscaler;
    if (navigator.clipboard) navigator.clipboard.writeText(d.command + "\n" + d.upscaler);
    log(txt);
    toast("Команда в журнале и в буфере обмена");
  }).catch(function (e) { toast(e.message, true); });
}

/* ---------- инструменты (вкладки) ---------- */

var T = {
  op: null,
  schema: null,
  token: null,
  meta: null,
  job: null,
  timer: null,
  logSeen: 0
};

function showUpscale() {
  S.tab = "upscale";
  openGroupFor("upscale");
  $("upscaleview").hidden = false;
  $("toolview").hidden = true;
  renderTabs(S.tabs, S.groups);
}

function showTool(opId) {
  S.tab = opId;
  openGroupFor(opId);
  if (opId === "p-upscale") return showImgup();
  stopToolPoll();
  T = { op: opId, schema: null, token: null, meta: null, job: null, timer: null,
        logSeen: 0, token2: null, meta2: null };
  $("upscaleview").hidden = true;
  $("toolview").hidden = false;
  renderTabs(S.tabs, S.groups);
  $("tool-title").textContent = "Загрузка…";
  $("tool-desc").textContent = "";
  $("tool-form").innerHTML = "";
  $("tool-fileinfo").hidden = true;
  $("tool-fileinfo2").hidden = true;
  $("tool-meta").innerHTML = "";
  $("tool-preview").hidden = true;
  $("tool-preview2").hidden = true;
  $("tool-result").hidden = true;
  $("tool-progress").hidden = true;
  $("tool-log").textContent = "";
  $("tool-estimate").textContent = "";
  $("tool-why").textContent = "";
  $("sec-tool2").hidden = true;
  $("sec-scrub").hidden = true;
  get("/api/media/ops/" + opId).then(function (d) {
    T.schema = d.op;
    $("tool-title").textContent = d.op.title;
    $("tool-desc").textContent = d.op.desc || "";
    var acc = (d.op.accept || "").replace(/,/g, ", ");
    $("tool-drop-hint").textContent = acc + " · или нажмите для выбора";
    $("tool-file").setAttribute("accept", d.op.accept || "*/*");
    setupSecondFile(d.op);
    renderToolForm(d.op);
    if (d.op.scrub) $("sec-scrub").hidden = false;
    tlog("Готов: " + d.op.title);
  }).catch(function (e) {
    $("tool-title").textContent = "Ошибка";
    tlog("Не открылось: " + e.message, "error");
  });
}

/* ---------- нарезка мышкой ---------- */

/* Поля start/duration остаются в схеме операции, но задавать их руками
   неудобно. Полоса ниже — это те же два числа, просто их можно тянуть. */
var SCRUB = { total: 0, start: 0, end: 0, min: 0.2, frame: null };

function fmtTc(s) {
  s = Math.max(0, Number(s) || 0);
  var m = Math.floor(s / 60);
  var sec = s - m * 60;
  return m + ":" + (sec < 10 ? "0" : "") + sec.toFixed(1);
}

function scrubEnabled() {
  return !!(T.schema && T.schema.scrub);
}

function setupScrub() {
  $("sec-scrub").hidden = !scrubEnabled();
  if (!scrubEnabled()) return;
  SCRUB.total = Number((T.meta && T.meta.duration) || 0);
  if (!(SCRUB.total > 0)) {
    SCRUB.total = Number((T.meta && T.meta.frames) || 0) / 25;
  }
  SCRUB.min = Math.min(0.2, SCRUB.total || 0.2);
  SCRUB.start = 0;
  SCRUB.end = SCRUB.total > 20 ? Math.min(10, SCRUB.total) : SCRUB.total;
  if (SCRUB.end <= SCRUB.start) SCRUB.end = SCRUB.total;
  SCRUB.frame = null;
  $("scrub-img").hidden = true;
  var scale = $("scrub-scale");
  scale.innerHTML = "";
  var marks = Math.min(6, Math.max(2, Math.round(SCRUB.total / 5)));
  for (var i = 0; i <= marks; i++) {
    var s = document.createElement("span");
    s.textContent = fmtTc(SCRUB.total * i / marks);
    scale.appendChild(s);
  }
  paintScrub();
  scrubFrame();
}

function paintScrub() {
  var total = SCRUB.total || 1;
  var a = (SCRUB.start / total) * 100;
  var b = (SCRUB.end / total) * 100;
  $("scrub-sel").style.left = a + "%";
  $("scrub-sel").style.width = Math.max(0, b - a) + "%";
  $("scrub-h0").style.left = a + "%";
  $("scrub-h1").style.left = b + "%";
  $("scrub-start").textContent = fmtTc(SCRUB.start);
  $("scrub-end").textContent = fmtTc(SCRUB.end);
  $("scrub-len").textContent = fmtTc(SCRUB.end - SCRUB.start);
  var f0 = document.getElementById("tf-start"), f1 = document.getElementById("tf-duration");
  if (f0) f0.value = SCRUB.start.toFixed(2);
  if (f1) f1.value = Math.max(0.05, SCRUB.end - SCRUB.start).toFixed(2);
}

var scrubFrameTimer = null;
function scrubFrame() {
  if (!T.meta || T.meta.kind !== "video") return;
  var at = SCRUB.start + (SCRUB.end - SCRUB.start) * 0.5;
  clearTimeout(scrubFrameTimer);
  scrubFrameTimer = setTimeout(function () {
    var img = $("scrub-img");
    img.src = "/api/media/cut-frame/" + encodeURIComponent(T.meta.token) +
      "?at=" + at.toFixed(2) + "&width=480&t=" + Date.now();
    img.hidden = false;
  }, 160);
}

function scrubSet(start, end) {
  var total = SCRUB.total || 0;
  start = clamp(Number(start) || 0, 0, total);
  end = clamp(Number(end) || 0, 0, total);
  if (end - start < SCRUB.min) {
    if (end > start) end = Math.min(total, start + SCRUB.min);
    else start = Math.max(0, end - SCRUB.min);
  }
  SCRUB.start = start;
  SCRUB.end = end;
  paintScrub();
  scrubFrame();
}

/* Позиция курсора по полосе -> секунда. */
function scrubTimeAt(clientX) {
  var box = $("scrub-track").getBoundingClientRect();
  if (!box.width || !SCRUB.total) return 0;
  return clamp((clientX - box.left) / box.width, 0, 1) * SCRUB.total;
}

function setupScrubDrag() {
  var track = $("scrub-track");
  var drag = null;

  function down(which, ev) {
    if (ev.button !== undefined && ev.button !== 0) return;
    drag = { which: which, x: ev.clientX, start: SCRUB.start, end: SCRUB.end };
    if (ev.preventDefault) ev.preventDefault();
  }

  ["scrub-h0", "scrub-h1"].forEach(function (id) {
    var el = $(id);
    on(el, "mousedown", function (ev) { down(id === "scrub-h0" ? 0 : 1, ev); });
  });
  on(track, "mousedown", function (ev) {
    if (ev.target && (ev.target.id === "scrub-h0" || ev.target.id === "scrub-h1")) return;
    var t = scrubTimeAt(ev.clientX);
    // Клик по самому выделению двигает весь кусок, клик по пустому полю —
    // ставит начало в эту точку.
    if (t > SCRUB.start && t < SCRUB.end) {
      var len = SCRUB.end - SCRUB.start;
      drag = { which: 2, x: ev.clientX, start: SCRUB.start, end: SCRUB.end, grab: t - SCRUB.start, len: len };
    } else {
      scrubSet(t, Math.min(SCRUB.total, t + SCRUB.min));
    }
  });

  on(window, "mousemove", function (ev) {
    if (!drag) return;
    if (drag.which === 2) {
      var box = track.getBoundingClientRect();
      var shift = (ev.clientX - drag.x) / (box.width || 1) * SCRUB.total;
      var len = drag.len;
      var s = clamp(drag.grab + shift, 0, Math.max(0, SCRUB.total - len));
      scrubSet(s, s + len);
      return;
    }
    var now = scrubTimeAt(ev.clientX);
    if (drag.which === 0) scrubSet(Math.min(now, SCRUB.end - SCRUB.min), SCRUB.end);
    else scrubSet(SCRUB.start, Math.max(now, SCRUB.start + SCRUB.min));
  });

  function up() { drag = null; }
  on(window, "mouseup", up);
  on(window, "blur", up);

  on($("scrub-min"), "click", function () {
    var len = SCRUB.end - SCRUB.start;
    var cut = len * 0.5;
    var mid = SCRUB.start + len / 2;
    scrubSet(mid - cut / 2, mid + cut / 2);
  });
  on($("scrub-max"), "click", function () {
    var len = SCRUB.end - SCRUB.start;
    var mid = SCRUB.start + len / 2;
    var add = Math.min(SCRUB.total - len, len);
    scrubSet(mid - (len + add) / 2, mid + (len + add) / 2);
  });
  on($("scrub-in"), "click", function () {
    var len = SCRUB.end - SCRUB.start;
    scrubSet(0, Math.min(SCRUB.total, len));
  });
  on($("scrub-out"), "click", function () {
    var len = SCRUB.end - SCRUB.start;
    scrubSet(Math.max(0, SCRUB.total - len), SCRUB.total);
  });
}

/* Человек вписал секунды руками — полоса обязана показать то же самое. */
function syncScrubFromFields() {
  if (!scrubEnabled() || !T.meta) return;
  var f0 = document.getElementById("tf-start");
  var f1 = document.getElementById("tf-duration");
  if (!f0 || !f1 || document.activeElement === f0 || document.activeElement === f1) return;
  var s = Number(f0.value) || 0;
  var d = Number(f1.value) || 0;
  if (d > 0) scrubSet(s, s + d);
}



/* ---------- апскейл фото нейросетью ---------- */

var IMGUP_SCHEMA = {
  id: "p-upscale",
  title: "Апскейл фото",
  desc: "Нейросеть увеличивает картинку. Модель скачается сама при первом запуске.",
  accept: "image/*",
  kinds: ["image"],
  submit: "Увеличить фото",
  fields: [
    { key: "engine", label: "Движок", type: "select", def: "auto",
      options: [
        { id: "auto", title: "Авто — GPU, иначе процессор" },
        { id: "gpu", title: "GPU · Real-ESRGAN" },
        { id: "srmd", title: "GPU · SRMD (быстро, для людей)" },
        { id: "cpu", title: "Процессор" }
      ],
      help: "Нейросеть умеет целые ×2/×3/×4. Недостающие пиксели добираются ресайзом." },
    { key: "model", label: "Модель", type: "select", def: "", options: [],
      help: "Для людей — general x4 или SRMD ×2. Для рисунков и аниме — anime." },
    { key: "scale_mode", label: "Как увеличить", type: "select", def: "percent",
      options: [
        { id: "percent", title: "Во сколько раз / процент" },
        { id: "long_edge", title: "Длинная сторона, px" },
        { id: "pixels", title: "Точные пиксели" }
      ] },
    { key: "percent", label: "Увеличение, %", type: "number", def: 200, min: 100, max: 800,
      step: 10, show_if: { key: "scale_mode", val: "percent" } },
    { key: "long_edge", label: "Длинная сторона, px", type: "number", def: 1920, min: 256,
      max: 7680, step: 16, show_if: { key: "scale_mode", val: "long_edge" } },
    { key: "width", label: "Ширина, px", type: "number", def: 1920, min: 2, max: 16384,
      step: 2, show_if: { key: "scale_mode", val: "pixels" } },
    { key: "height", label: "Высота, px", type: "number", def: 1080, min: 2, max: 16384,
      step: 2, show_if: { key: "scale_mode", val: "pixels" } },
    { key: "tta", label: "TTA — медленнее, но качественнее", type: "check", def: false },
    { key: "tile", label: "Тайл, 0 — авто", type: "number", def: 0, min: 0, max: 512, step: 32 },
    { key: "format", label: "Формат", type: "select", def: "keep",
      options: [
        { id: "keep", title: "Как в исходнике" },
        { id: "jpg", title: "JPG" },
        { id: "png", title: "PNG" },
        { id: "webp", title: "WebP" }
      ] },
    { key: "quality", label: "Качество", type: "number", def: 92, min: 1, max: 100 }
  ]
};

function showImgup() {
  stopToolPoll();
  T = { op: "p-upscale", schema: IMGUP_SCHEMA, token: null, meta: null, job: null,
        timer: null, logSeen: 0, token2: null, meta2: null };
  $("upscaleview").hidden = true;
  $("toolview").hidden = false;
  $("tool-title").textContent = IMGUP_SCHEMA.title;
  $("tool-desc").textContent = IMGUP_SCHEMA.desc;
  $("tool-form").innerHTML = "";
  $("tool-fileinfo").hidden = true;
  $("tool-fileinfo2").hidden = true;
  $("tool-meta").innerHTML = "";
  $("tool-preview").hidden = true;
  $("tool-preview2").hidden = true;
  $("tool-result").hidden = true;
  $("tool-progress").hidden = true;
  $("tool-log").textContent = "";
  $("tool-estimate").textContent = "";
  $("tool-why").textContent = "";
  $("sec-tool2").hidden = true;
  $("tool-drop-hint").textContent = "JPG, PNG, WebP, BMP, TIFF · или нажмите для выбора";
  $("tool-file").setAttribute("accept", "image/*");
  renderToolForm(IMGUP_SCHEMA);
  imgupModels();
  on($("tf-engine"), "change", imgupModels);
  tlog("Готов: " + IMGUP_SCHEMA.title);
}

function imgupEngine() {
  var sel = $("tf-engine");
  var eng = sel ? sel.value : "auto";
  if (eng !== "auto") return eng;
  var h = S.health;
  if (h && h.devices && h.devices.gpu && h.devices.gpu.available) return "gpu";
  return "cpu";
}

function imgupModels() {
  var sel = $("tf-model");
  if (!sel) return;
  var engine = imgupEngine();
  var list = (S.models || []).filter(function (m) {
    return (m.engines || []).indexOf(engine) >= 0;
  });
  var cur = sel.value;
  sel.innerHTML = "";
  if (!list.length) {
    var none = document.createElement("option");
    none.value = "";
    none.textContent = "Нет моделей для этого движка";
    sel.appendChild(none);
    return;
  }
  list.forEach(function (m) {
    var o = document.createElement("option");
    o.value = m.id;
    o.textContent = m.title + " ×" + m.scale + (m.installed ? "" : " (скачать " + m.size_mb + " МБ)");
    sel.appendChild(o);
  });
  if (list.some(function (m) { return m.id === cur; })) {
    sel.value = cur;
  } else {
    var def = list.filter(function (m) { return m.default; })[0] || list[0];
    sel.value = def.id;
  }
}

/* Что получится из выбранных полей — показываем до запуска. */
function imgupPlan() {
  if (!T.meta) return "";
  var o = readTool();
  var sw = Number(T.meta.width) || 0, sh = Number(T.meta.height) || 0;
  var tw = sw, th = sh;
  if (o.scale_mode === "percent") {
    var f = (Number(o.percent) || 200) / 100;
    tw = sw * f; th = sh * f;
  } else if (o.scale_mode === "long_edge") {
    var e = Number(o.long_edge) || 1920;
    if (sw >= sh) { tw = e; th = e * sh / sw; } else { th = e; tw = e * sw / sh; }
  } else {
    tw = Number(o.width) || sw; th = Number(o.height) || sh;
  }
  tw = Math.max(2, Math.round(tw));
  th = Math.max(2, Math.round(th));
  return sw + "×" + sh + " → " + tw + "×" + th + " (×" + (Math.round(tw / sw * 100) / 100) + ")";
}

function imgupStart() {
  var o = readTool();
  o.even_only = false;
  o.format = o.format || "keep";
  tlog("Старт: " + imgupPlan());
  post("/api/imgup/jobs", { token: T.token, options: o }).then(function (d) {
    T.job = d.job;
    T.logSeen = 0;
    paintToolJob(T.job);
    toolPoll();
  }).catch(function (e) {
    tlog("Не запустилось: " + e.message, "error");
    toast(e.message, true);
  });
}


function tlog(text, cls) {
  var box = $("tool-log");
  var line = document.createElement("div");
  if (cls) line.className = cls;
  line.textContent = "[" + stamp() + "] " + text;
  box.appendChild(line);
  while (box.children.length > 150) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
}

function renderToolForm(schema) {
  var host = $("tool-form");
  host.innerHTML = "";
  (schema.fields || []).forEach(function (f) {
    var wrap = document.createElement("div");
    var lab = document.createElement("label");
    lab.className = "lbl";
    lab.textContent = f.label;
    wrap.appendChild(lab);
    if (f.help) {
      var help = document.createElement("div");
      help.className = "dim";
      help.style.fontSize = "13px";
      help.textContent = f.help;
      wrap.appendChild(help);
    }
    var input = null;
    if (f.type === "select") {
      input = document.createElement("select");
      input.className = "inp";
      (f.options || []).forEach(function (o) {
        var op = document.createElement("option");
        op.value = o.id;
        op.textContent = o.title;
        input.appendChild(op);
      });
      input.value = f.def;
    } else if (f.type === "check") {
      var lab2 = document.createElement("label");
      lab2.className = "check";
      input = document.createElement("input");
      input.type = "checkbox";
      input.checked = !!f.def;
      var sp = document.createElement("span");
      sp.textContent = f.label;
      lab2.appendChild(input);
      lab2.appendChild(sp);
      lab.textContent = "";
      wrap.appendChild(lab2);
    } else {
      input = document.createElement("input");
      input.className = "inp num";
      input.type = "number";
      if (f.min !== undefined) input.min = f.min;
      if (f.max !== undefined) input.max = f.max;
      if (f.step !== undefined) input.step = f.step;
      input.value = f.def;
    }
    if (input) {
      input.id = "tf-" + f.key;
      if (f.type !== "check") wrap.appendChild(input);
      on(input, "change", function () { syncConds(); syncScrubFromFields(); });
    }
    if (f.show_if) {
      wrap.setAttribute("data-showif", f.show_if.key + "=" + f.show_if.val);
    }
    host.appendChild(wrap);
  });
  var runRow = $("tool-run").parentNode;
  runRow.style.display = schema.submit ? "" : "none";
  if (schema.submit) $("tool-run").textContent = schema.submit;
  syncConds();
}

/* Поля с show_if видны только когда поле-условие имеет нужное значение:
   «Процент» прячет «Длинную сторону», «Пиксели» — «Процент». */
function syncConds() {
  var host = $("tool-form");
  if (!host) return;
  var kids = host.children;
  for (var i = 0; i < kids.length; i++) {
    var cond = kids[i].getAttribute && kids[i].getAttribute("data-showif");
    if (!cond) continue;
    var p = cond.split("=");
    var src = document.getElementById("tf-" + p[0]);
    kids[i].hidden = !(src && src.value === p[1]);
  }
}

function readTool() {
  var o = {};
  ((T.schema && T.schema.fields) || []).forEach(function (f) {
    var el = document.getElementById("tf-" + f.key);
    if (!el) return;
    if (f.type === "check") o[f.key] = el.checked;
    else if (f.type === "number") o[f.key] = Number(el.value);
    else o[f.key] = el.value;
  });
  return o;
}

function setupToolUI() {
  var drop = $("tool-drop"), input = $("tool-file");
  on(drop, "click", function () { input.click(); });
  on(input, "change", function () { if (input.files[0]) toolUpload(input.files[0]); });
  ["dragenter", "dragover"].forEach(function (ev) {
    on(drop, ev, function (e) { e.preventDefault(); drop.classList.add("over"); });
  });
  ["dragleave", "drop"].forEach(function (ev) {
    on(drop, ev, function (e) { e.preventDefault(); drop.classList.remove("over"); });
  });
  on(drop, "drop", function (e) {
    var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) toolUpload(f);
  });
  var drop2 = $("tool-drop2"), input2 = $("tool-file2");
  on(drop2, "click", function () { input2.click(); });
  on(input2, "change", function () { if (input2.files[0]) toolUpload2(input2.files[0]); });
  ["dragenter", "dragover"].forEach(function (ev) {
    on(drop2, ev, function (e) { e.preventDefault(); drop2.classList.add("over"); });
  });
  ["dragleave", "drop"].forEach(function (ev) {
    on(drop2, ev, function (e) { e.preventDefault(); drop2.classList.remove("over"); });
  });
  on(drop2, "drop", function (e) {
    var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) toolUpload2(f);
  });
  on($("tool-run"), "click", toolStart);
  on($("tool-cancel"), "click", toolCancel);
  setupScrubDrag();
}

/* Второй файл нужен только инструментам со схемой files > 1 («Видео + аудио»). */
function setupSecondFile(schema) {
  var need = schema && Number(schema.files) > 1;
  $("sec-tool2").hidden = !need;
  if (!need) {
    T.token2 = null;
    T.meta2 = null;
    $("tool-fileinfo2").hidden = true;
    $("tool-preview2").hidden = true;
    return;
  }
  $("tool2-title").textContent = schema.title || "Второй файл";
  $("tool-file2").setAttribute("accept", schema.accept2 || "audio/*");
  var acc = (schema.accept2 || "audio/*").replace(/,/g, ", ");
  $("tool-drop2-hint").textContent = acc + " · или нажмите для выбора";
  $("tool-fileinfo2").hidden = !T.meta2;
  $("tool-preview2").hidden = true;
}

function toolUpload2(file) {
  tlog("Второй файл: " + file.name + " (" + fmtBytes(file.size) + ")…");
  var fd = new FormData();
  fd.append("file", file, file.name);
  fetch("/api/media/probe", { method: "POST", body: fd }).then(function (r) {
    return r.json().then(function (body) {
      if (!r.ok) throw new Error(body.error || ("HTTP " + r.status));
      return body;
    });
  }).then(function (data) {
    if (data.meta.kind !== "audio") {
      throw new Error("Вторым файлом должна быть музыка, а не " + data.meta.kind);
    }
    T.meta2 = data.meta;
    T.token2 = data.meta.token;
    $("tool-fileinfo2").hidden = false;
    $("tool-fileinfo2").textContent = data.meta.name + " · " + fmtBytes(data.meta.size_bytes) +
      " · " + describeMeta(data.meta);
    var pv = $("tool-preview2");
    pv.hidden = false;
    pv.innerHTML = '<audio controls preload="metadata" src="/api/media/file/' +
      encodeURIComponent(T.token2) + '?inline=1"></audio>';
    tlog("Второй файл готов: " + data.meta.name, "ok");
  }).catch(function (e) {
    T.token2 = null;
    $("tool-fileinfo2").hidden = true;
    tlog("Второй файл не принят: " + e.message, "error");
    toast(e.message, true);
  });
}

function toolBusy() {
  return T.job && ["queued", "probing", "preparing", "decoding", "upscaling", "encoding", "assembling"].indexOf(T.job.stage) >= 0;
}

function toolUpload(file) {
  if (!T.schema) { toast("Сначала откройте вкладку", true); return; }
  tlog("Загрузка " + file.name + " (" + fmtBytes(file.size) + ")…");
  var fd = new FormData();
  fd.append("file", file, file.name);
  fetch("/api/media/probe", { method: "POST", body: fd }).then(function (r) {
    return r.json().then(function (body) {
      if (!r.ok) throw new Error(body.error || ("HTTP " + r.status));
      return body;
    });
  }).then(function (data) {
    var kind = data.meta.kind;
    if (T.schema.kinds.indexOf(kind) < 0) {
      throw new Error("Сюда нужен другой файл: " + T.schema.kinds.join(", "));
    }
    T.meta = data.meta;
    T.token = data.meta.token;
    renderToolFile();
    tlog("Файл разобран: " + describeMeta(data.meta), "ok");
  }).catch(function (e) {
    tlog("Ошибка: " + e.message, "error");
    toast(e.message, true);
  });
}

function describeMeta(m) {
  if (m.kind === "video") return m.width + "×" + m.height + ", " + m.fps + " fps, " + fmtDur(m.duration);
  if (m.kind === "image") return m.width + "×" + m.height + ", " + m.video_codec;
  if (m.kind === "audio") return (m.audio_codec || "?") + ", " + fmtDur(m.duration);
  return m.name || "?";
}

function metaRows(m) {
  if (m.kind === "video") {
    return [
      ["Разрешение", m.width + "×" + m.height],
      ["Частота кадров", m.fps + " fps"],
      ["Длительность", fmtDur(m.duration)],
      ["Кадров", String(m.frames)],
      ["Видеокодек", m.video_codec],
      ["Аудио", m.has_audio ? m.audio_codec : "нет"],
      ["Битрейт", fmtBitrate(m.bitrate)],
      ["Размер файла", fmtBytes(m.size_bytes)]
    ];
  }
  if (m.kind === "image") {
    return [
      ["Разрешение", m.width + "×" + m.height],
      ["Формат", m.video_codec],
      ["Размер файла", fmtBytes(m.size_bytes)]
    ];
  }
  return [
    ["Длительность", fmtDur(m.duration)],
    ["Кодек", m.audio_codec || "?"],
    ["Каналов", String(m.audio_channels || "?")],
    ["Частота", String(m.audio_rate || "?") + " Гц"],
    ["Размер файла", fmtBytes(m.size_bytes)]
  ];
}

function renderToolFile() {
  var m = T.meta;
  var fi = $("tool-fileinfo");
  fi.hidden = false;
  fi.textContent = m.name + " · " + fmtBytes(m.size_bytes);
  var html = "";
  metaRows(m).forEach(function (r) {
    html += "<tr><td>" + r[0] + "</td><td>" + esc(String(r[1])) + "</td></tr>";
  });
  var mt = $("tool-meta");
  mt.innerHTML = '<table class="meta"><tbody>' + html + "</tbody></table>";
  var pv = $("tool-preview");
  if (m.kind === "video") {
    // Видео показываем плеером: один кадр не говорит, что загрузилось.
    pv.hidden = false;
    pv.innerHTML = '<video controls preload="metadata" playsinline src="' +
      "/api/media/file/" + encodeURIComponent(m.token) + '?inline=1"></video>';
  } else if (m.preview_url) {
    pv.hidden = false;
    pv.innerHTML = m.kind === "audio"
      ? '<audio controls preload="metadata" src="' + m.preview_url + '"></audio>'
      : '<img src="' + m.preview_url + '" alt="превью">';
  } else {
    pv.hidden = true;
  }
  if (T.op === "meta") {
    tlog("Метаданные выше — задача не нужна", "ok");
  }
  if (T.op === "p-upscale") {
    $("tool-estimate").textContent = "Итог: " + imgupPlan();
    $("tool-why").textContent = "Проверьте модель и нажмите «Увеличить фото».";
  }
  if (scrubEnabled()) setupScrub();
}

function toolStart() {
  if (!T.schema) return;
  if (!T.token || !T.meta) {
    toast("Сначала загрузите файл", true);
    return;
  }
  if (toolBusy()) {
    toast("Задача уже идёт", true);
    return;
  }
  if (T.op === "p-upscale") return imgupStart();
  if (Number(T.schema.files) > 1 && !T.token2) {
    toast("Нужен второй файл — перетащите его в поле ниже", true);
    return;
  }
  var o = readTool();
  tlog("Старт: " + T.schema.title);
  var payload = { op: T.op, token: T.token, options: o };
  if (Number(T.schema.files) > 1) payload.token2 = T.token2;
  post("/api/media/jobs", payload).then(function (d) {
    T.job = d.job;
    T.logSeen = 0;
    paintToolJob(T.job);
    toolPoll();
  }).catch(function (e) {
    tlog("Не запустилось: " + e.message, "error");
    toast(e.message, true);
  });
}

function toolCancel() {
  if (!T.job || !toolBusy()) {
    toast("Нет активной задачи");
    return;
  }
  post("/api/jobs/" + T.job.id + "/cancel").catch(function (e) { toast(e.message, true); });
}

function stopToolPoll() {
  clearTimeout(T.timer);
  T.timer = null;
}

function toolPoll() {
  stopToolPoll();
  if (!T.job) return;
  T.timer = setTimeout(function () {
    get("/api/jobs/" + T.job.id).then(function (d) {
      if (!T.job || d.job.id !== T.job.id) return;
      T.job = d.job;
      paintToolJob(T.job);
      toolTailLogs();
      if (toolBusy()) toolPoll();
      else toolFinished(T.job);
    }).catch(function () { if (toolBusy()) toolPoll(); });
  }, 800);
}

function toolTailLogs() {
  if (!T.job) return;
  get("/api/jobs/" + T.job.id + "/logs?since=" + T.logSeen).then(function (d) {
    (d.logs || []).forEach(function (e) { tlog(e.text, e.level === "error" ? "error" : ""); });
    T.logSeen += (d.logs || []).length;
  }).catch(function () {});
}

function paintToolJob(j) {
  $("tool-progress").hidden = false;
  $("tool-stage").textContent = j.message || j.stage;
  $("tool-pct").textContent = Math.round(j.percent) + "%";
  $("tool-fill").style.width = j.percent + "%";
  var stats = "";
  if (j.frames_total > 1000 || j.frame > 0) stats = "кадр " + j.frame + " / " + j.frames_total;
  if (j.speed) stats += (stats ? " · " : "") + j.speed + " кадров/с";
  if (j.eta) stats += (stats ? " · " : "") + "осталось " + fmtLeft(j.eta);
  $("tool-stats").textContent = stats;
  $("tool-why").textContent = "";
}

function toolFinished(j) {
  toolTailLogs();
  var r = $("tool-result");
  if (j.stage === "done" && j.output) {
    r.hidden = false;
    paintResult(r, j);
    tlog("Готово: " + j.output, "ok");
    toast("Готово: " + j.output);
  } else if (j.stage === "failed") {
    r.hidden = false;
    r.innerHTML = '<span style="color:var(--bad)">Ошибка: ' + esc(j.error || "?") + "</span>";
    tlog("Ошибка: " + (j.error || "?"), "error");
    toast(j.error || "Задача провалилась", true);
  } else {
    tlog("Задача отменена", "warn");
  }
}

/* ---------- старт ---------- */

function boot() {
  // Три независимых пути клика — чтобы его было негде потерять:
  // 1) прямое навешивание, 2) инлайн onclick в разметке, 3) делегирование
  // на document в capture-фазе. Плюс проверка дублей id (именно дубль
  // id="start" однажды убил кнопку: getElementById вернул поле ввода).
  ["runbtn", "stopbtn", "copycmd"].forEach(function (id) {
    var b = $(id);
    if (b) { b.removeAttribute("disabled"); b.disabled = false; }
  });
  var seen = {}, dupes = [];
  var all = document.querySelectorAll("[id]");
  for (var qi = 0; qi < all.length; qi++) {
    if (seen[all[qi].id]) dupes.push(all[qi].id);
    seen[all[qi].id] = 1;
  }
  if (dupes.length) {
    errBox("Битая разметка: повторяющиеся id: " + dupes.join(", "));
    log("Дубли id: " + dupes.join(", "), "error");
  }
  log("PedroDevTools — запуск");
  initTheme();
  initMode();
  renderGoals();
  setupFile();
  setupInputs();
  setupToolUI();
  showUpscale();
  on($("runbtn"), "click", start);
  on($("stopbtn"), "click", cancel);
  on($("copycmd"), "click", copyCmd);
  window.__pedroStart = start;
  window.__pedroCancel = cancel;
  document.addEventListener("click", function (e) {
    var t = e.target && e.target.closest ? e.target.closest("#runbtn,#stopbtn") : null;
    if (!t) return;
    // Событие передаём дальше: один физический клик — один запуск,
    // даже если сработают и прямой хендлер, и onclick, и это делегирование.
    if (t.id === "runbtn") start(e);
    else cancel(e);
  }, true);

  get("/api/tabs").then(function (d) {
    // Состояние папок помнится между запусками. Старое одиночное pd_group
    // игнорируем: раньше открывалась ровно одна группа, теперь — любая.
    try {
      var saved = JSON.parse(localStorage.getItem("pd_groups") || "{}");
      if (saved && typeof saved === "object") S.openGroups = saved;
    } catch (e) { S.openGroups = {}; }
    renderTabs(d.tabs || [], d.groups || []);
    return get("/api/health");
  }).then(function (h) {
    S.health = h;
    renderHealth(h);
    if (!h.ok) {
      errBox("ffmpeg не найден в PATH — обработка невозможна.");
      log("ffmpeg не найден", "error");
    }
    if (!h.devices.gpu.available) log("GPU недоступен: " + h.devices.gpu.note, "warn");
    return get("/api/settings");
  }).then(function (d) {
    return refreshModels().then(function () {
      applySettings(d.settings || {});
      renderEngineNote();
      renderModels();
      ensureModel();
      return get("/api/presets");
    });
  }).then(function (d) {
    S.presets = d.built_in || [];
    renderPresets(S.presets);
    recalc();
    window.__booted = true;
    log("Готово к работе", "ok");
  }).catch(function (e) {
    errBox("Не могу связаться с сервером: " + e.message + ". Проверьте, что запущен run.bat, и обновите страницу.");
    log("Сервер недоступен: " + e.message, "error");
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}

})();
