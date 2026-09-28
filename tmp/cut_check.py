"""Проверка новых кусков: размер без пропорций, предпросмотр видео, кадр нарезки."""
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import config  # noqa: E402
import ffmpeg  # noqa: E402

BASE = "http://127.0.0.1:8756"
ROOT = Path(__file__).resolve().parent
fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ((" — " + str(extra)) if extra else ""))
    if not cond:
        fails.append(name)


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return r.status, r.headers, r.read()


def post(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def upload(name):
    import uuid
    b = "B" + uuid.uuid4().hex
    body = (("--%s\r\n" % b).encode()
            + ('Content-Disposition: form-data; name="file"; filename="%s"\r\n\r\n' % name).encode()
            + (ROOT / name).read_bytes() + ("\r\n--%s--\r\n" % b).encode())
    req = urllib.request.Request(BASE + "/api/media/probe", data=body,
                                 headers={"Content-Type": "multipart/form-data; boundary=" + b})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())["meta"]


# 1. Размер без пропорций -> ровно 200x100
vm = upload("fx_v.mp4")
job = post("/api/media/jobs", {"op": "v-size", "token": vm["token"],
                               "options": {"width": 200, "height": 100, "keep_ratio": False,
                                           "codec": "libx264", "crf": 23,
                                           "x264_preset": "veryfast", "container": "mp4",
                                           "audio": "copy", "audio_bitrate": 128}})["job"]
import time
while True:
    job = json.loads(get("/api/jobs/" + job["id"])[2].decode())["job"]
    if job["stage"] in ("done", "failed", "cancelled"):
        break
    time.sleep(1)
out = job.get("output_meta") or {}
check("видео без пропорций = 200x100", job["stage"] == "done" and out.get("width") == 200
      and out.get("height") == 100, "%s %sx%s" % (job["stage"], out.get("width"), out.get("height")))

# 2. С пропорциями ширина 160 -> высота 90 (исходник 320x180)
job = post("/api/media/jobs", {"op": "v-size", "token": vm["token"],
                               "options": {"width": 160, "height": 0, "keep_ratio": True,
                                           "codec": "libx264", "crf": 23,
                                           "x264_preset": "veryfast", "container": "mp4",
                                           "audio": "copy", "audio_bitrate": 128}})["job"]
while True:
    job = json.loads(get("/api/jobs/" + job["id"])[2].decode())["job"]
    if job["stage"] in ("done", "failed", "cancelled"):
        break
    time.sleep(1)
out = job.get("output_meta") or {}
check("видео с пропорциями = 160x90", job["stage"] == "done" and out.get("width") == 160
      and out.get("height") == 90, "%s %sx%s" % (job["stage"], out.get("width"), out.get("height")))

# 3. Тот же файл отдаётся как видео (плеер), а не как вложение
status, headers, data = get("/api/media/file/%s?inline=1" % vm["token"])
check("исходник отдаётся как video/mp4", headers.get("content-type") == "video/mp4",
      headers.get("content-type"))
check("нет вложения (браузер играет)", "attachment" not in (headers.get("content-disposition") or ""),
      headers.get("content-disposition"))
check("размер совпадает", len(data) == (ROOT / "fx_v.mp4").stat().st_size)

# 4. Кадр для полосы нарезки
status, headers, data = get("/api/media/cut-frame/%s?at=0.5&width=480" % vm["token"])
check("кадр нарезки отдаётся PNG", status == 200 and data[:8] == b"\x89PNG\r\n\x1a\n",
      headers.get("content-type"))

# 5. Для аудио кадр запрашивать бессмысленно — должен быть понятный отказ
am = upload("fx_a.mp3")
try:
    get("/api/media/cut-frame/%s?at=0.5" % am["token"])
    check("аудио без кадра", False, "ответ без ошибки")
except urllib.error.HTTPError as exc:
    check("аудио без кадра", exc.code == 400, "HTTP %s" % exc.code)

# 6. У audio/видео теперь есть превью для плеера
check("аудио получило preview_url", bool(am.get("preview_url")), am.get("preview_url"))
status, headers, data = get("/api/media/file/%s?inline=1" % am["token"])
check("аудио отдаётся как audio/mpeg", headers.get("content-type") == "audio/mpeg",
      headers.get("content-type"))

# 7. Схема нарезки объявляет полосу
schema = json.loads(get("/api/media/ops/v-cut")[2].decode())["op"]
check("v-cut помечен как scrub", schema.get("scrub") is True)
schema = json.loads(get("/api/media/ops/a-cut")[2].decode())["op"]
check("a-cut помечен как scrub", schema.get("scrub") is True)
check("a-cut только для аудио", schema.get("kinds") == ["audio"])
check("у v-size есть галочка пропорций",
      any(f["key"] == "keep_ratio" for f in
          json.loads(get("/api/media/ops/v-size")[2].decode())["op"]["fields"]))
check("у p-size есть галочка пропорций",
      any(f["key"] == "keep_ratio" for f in
          json.loads(get("/api/media/ops/p-size")[2].decode())["op"]["fields"]))

# 8. Старых вкладок больше нет
tabs = json.loads(get("/api/tabs")[2].decode())["tabs"]
ids = [t["id"] for t in tabs]
check("вкладок v-res/p-res нет", "v-res" not in ids and "p-res" not in ids)
check("вкладка a-cut есть", "a-cut" in ids, "всего вкладок: %d" % len(ids))

print("FAIL count:", len(fails))
sys.exit(1 if fails else 0)
