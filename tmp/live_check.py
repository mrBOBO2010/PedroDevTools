"""Финальная проверка живого сервера: версия, второй файл, inline-типы, логотип."""
import json
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import config  # noqa: E402

BASE = "http://127.0.0.1:8756"
fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ((" — " + str(extra)) if extra else ""))
    if not cond:
        fails.append(name)


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return r.status, r.headers, r.read()


def upload(path):
    import uuid

    boundary = "B" + uuid.uuid4().hex
    data = Path(path).read_bytes()
    body = (("--%s\r\n" % boundary).encode()
            + ('Content-Disposition: form-data; name="file"; filename="%s"\r\n\r\n' % Path(path).name).encode()
            + data + ("\r\n--%s--\r\n" % boundary).encode())
    req = urllib.request.Request(BASE + "/api/media/probe", data=body,
                                 headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())["meta"]


health = json.loads(get("/api/health")[2].decode())
check("короткая версия ffmpeg — дата", health["ffmpeg_short"] == "2025-12-07", health["ffmpeg_short"])

audio = upload(Path(__file__).resolve().parent / "fx_a.mp3")
status, headers, body = get("/api/media/file/%s?inline=1" % audio["token"])
check("аудио отдаётся inline с audio/*", headers.get("content-type", "").startswith("audio/"),
      headers.get("content-type"))
check("размер совпадает", len(body) == (Path(__file__).resolve().parent / "fx_a.mp3").stat().st_size)

status, headers, body = get("/PedroDevLOGO.png")
check("логотип отдаётся", status == 200 and len(body) == 38715, "%s, %d байт" % (status, len(body)))
check("логотип — png", headers.get("content-type", "").startswith("image/png"), headers.get("content-type"))

status, headers, _ = get("/")
check("главная отдаётся", status == 200)
check("статика без кэша", "no-store" in (headers.get("cache-control") or ""), headers.get("cache-control"))

status, _, body = get("/app.js")
check("app.js без BOM и свежий", b"\xef\xbb\xbf" != body[:3] and b"3.2-groups-theme-imgup" in body)
check("app.js валиден UTF-8", body.decode("utf-8") is not None)

for asset in ("/style.css?v=3.2", "/index.html"):
    st, _, _ = get(asset)
    check("отдаётся " + asset, st == 200)

out = sorted(p.name for p in config.OUT_DIR.glob("*"))[:5]
print("последние результаты в output/:", ", ".join(out) or "(пусто)")

print("FAIL count:", len(fails))
sys.exit(1 if fails else 0)
