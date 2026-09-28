"""Живая проверка апскейла фото: реальный файл, реальный движок, реальный результат."""
import io
import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import config  # noqa: E402
import ffmpeg  # noqa: E402

BASE = "http://127.0.0.1:8756"
TMP = config.TMP_DIR
TMP.mkdir(parents=True, exist_ok=True)
src = TMP / ("e2e_{}.jpg".format(uuid.uuid4().hex[:6]))
cmd = [ffmpeg.find_binary("ffmpeg"), "-y", "-v", "error", "-f", "lavfi",
       "-i", "testsrc2=size=640x360:rate=1", "-frames:v", "1", str(src)]
assert ffmpeg.run(cmd).returncode == 0, "не создался тестовый кадр"
print("исходник:", src.name, src.stat().st_size, "байт")


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def post_json(path, payload):
    req = urllib.request.Request(BASE + path, method="POST",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


# 1. загрузка
body = "----pdt"
parts = []
for name, data, ctype in (("file", src.read_bytes(), "image/jpeg"),):
    parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                  "Content-Type: %s\r\n\r\n" % (body, name, src.name, ctype)).encode("utf-8")
                 + data + b"\r\n")
payload = b"".join(parts) + ("--%s--\r\n" % body).encode("utf-8")
req = urllib.request.Request(BASE + "/api/media/probe", data=payload,
                             headers={"Content-Type": "multipart/form-data; boundary=" + body})
with urllib.request.urlopen(req, timeout=120) as r:
    meta = json.loads(r.read().decode("utf-8"))["meta"]
print("разобрано:", meta["kind"], meta["width"], "x", meta["height"], "токен", meta["token"])
assert meta["kind"] == "image"

# 2. запуск: SRMD x2 — самая быстрая модель
options = {"engine": "srmd", "model": "srmd-x2", "scale_mode": "percent", "percent": 200,
           "even_only": False, "format": "keep", "quality": 92, "tta": False, "tile": 0}
job = post_json("/api/imgup/jobs", {"token": meta["token"], "options": options})["job"]
print("задача:", job["id"], job["stage"])
t0 = time.time()
while True:
    job = get("/api/jobs/" + job["id"])["job"]
    print("  %-11s %5.1f%%  %s" % (job["stage"], job["percent"], job["message"]))
    if job["stage"] in ("done", "failed", "cancelled") or time.time() - t0 > 600:
        break
    time.sleep(1.0)
print("итог:", job["stage"], job["error"] or "")
if job["stage"] != "done":
    for line in get("/api/jobs/" + job["id"] + "/logs")["logs"]:
        print("  лог:", line["text"])
    raise SystemExit("задача не завершилась")

out_meta = job["output_meta"] or {}
print("результат:", job["output"], job["output_size"], "байт",
      out_meta.get("width"), "x", out_meta.get("height"))
assert out_meta.get("width") == 1280 and out_meta.get("height") == 720, "размер не 1280x720"

# 3. файл реально отдаётся и открывается
out = config.OUT_DIR / job["output"]
assert out.exists(), "нет файла результата"
check = TMP / ("e2e_check_{}.png".format(uuid.uuid4().hex[:6]))
assert ffmpeg.run([ffmpeg.find_binary("ffmpeg"), "-y", "-v", "error", "-i", str(out),
                   "-frames:v", "1", str(check)]).returncode == 0, "результат не декодируется"
print("проверка декодирования: ok")
req = urllib.request.Request(BASE + "/api/jobs/" + job["id"] + "/download?inline=1")
with urllib.request.urlopen(req, timeout=60) as r:
    data = r.read()
    print("inline-выдача:", r.headers.get("content-type"), len(data), "байт")
    assert len(data) == out.stat().st_size
    assert r.headers.get("content-type", "").startswith("image/")

print("ИТОГ: фото-AI работает")
for p in (src, check):
    p.unlink(missing_ok=True)
