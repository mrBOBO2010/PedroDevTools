"""Живой тест медиа-инструментов: сам создаёт фикстуры и гоняет все 16 операций.

Запуск: сервер поднят на 127.0.0.1:8756, затем `python tmp/mediatest.py`.
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import config  # noqa: E402
import ffmpeg  # noqa: E402

BASE = "http://127.0.0.1:8756"
ROOT = Path(__file__).resolve().parent
FF = ffmpeg.find_binary("ffmpeg")


def api(method, path, data=None, files=None):
    if files:
        import uuid as _uuid

        boundary = "B" + _uuid.uuid4().hex
        body = b""
        for name, (fname, content) in files.items():
            body += ("--" + boundary + "\r\n").encode()
            body += ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n\r\n' % (name, fname)).encode()
            body += content + b"\r\n"
        body += ("--" + boundary + "--\r\n").encode()
        req = urllib.request.Request(BASE + path, data=body, method=method,
                                     headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    else:
        payload = json.dumps(data or {}).encode()
        req = urllib.request.Request(BASE + path, data=payload if method != "GET" else None,
                                     method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def wait_job(job_id, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        job = api("GET", "/api/jobs/" + job_id)["job"]
        if job["stage"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(1.5)
    raise RuntimeError("timeout on " + job_id)


def make_fixtures():
    """Фикстуры генерируются заново: тест не должен зависеть от мусора в tmp."""
    specs = [
        (ROOT / "fx_v.mp4", ["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10",
                             "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                             "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                             "-c:a", "aac", "-shortest"]),
        (ROOT / "fx_p.png", ["-f", "lavfi", "-i", "testsrc2=size=320x180", "-frames:v", "1"]),
        (ROOT / "fx_a.mp3", ["-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                             "-c:a", "libmp3lame"]),
    ]
    for path, args in specs:
        if not path.exists():
            code = subprocess.run([FF, "-y", "-v", "error"] + args + [str(path)]).returncode
            assert code == 0, "не создалась фикстура " + path.name
    return [p.name for p, _ in specs]


def upload(name):
    return api("POST", "/api/media/probe", files={"file": (name, (ROOT / name).read_bytes())})["meta"]


def run(op, token, options, token2=None):
    payload = {"op": op, "token": token, "options": options}
    if token2:
        payload["token2"] = token2
    return wait_job(api("POST", "/api/media/jobs", payload)["job"]["id"])


VOUT = {"codec": "libx264", "crf": 23, "x264_preset": "veryfast", "container": "mp4",
        "audio": "copy", "audio_bitrate": 128, "x264_preset": "veryfast"}

CASES = [
    ("v-size", "fx_v.mp4", dict(VOUT, width=160, height=0, keep_ratio=True)),
    ("v-size", "fx_v.mp4", dict(VOUT, width=200, height=100, keep_ratio=False, audio="none")),
    ("v-comp", "fx_v.mp4", dict(VOUT, crf=28, audio="aac", maxside="0")),
    ("v-speed", "fx_v.mp4", dict(VOUT, factor="2", audio="aac")),
    ("v-cut", "fx_v.mp4", dict(VOUT, start=0.5, duration=1, mode="copy")),
    ("v-rotate", "fx_v.mp4", dict(VOUT, mode="90", audio="aac")),
    ("v-audio", "fx_v.mp4", {"format": "mp3", "bitrate": 128}),
    ("v-gif", "fx_v.mp4", {"width": 160, "fps": 5, "start": 0, "duration": 1}),
    ("p-size", "fx_p.png", {"width": 160, "height": 0, "format": "jpg", "quality": 80, "keep_ratio": True}),
    ("p-size", "fx_p.png", {"width": 200, "height": 100, "format": "png", "quality": 80, "keep_ratio": False}),
    ("p-comp", "fx_p.png", {"format": "webp", "quality": 70, "maxside": "0"}),
    ("a-comp", "fx_a.mp3", {"format": "ogg", "bitrate": 96, "rate": "44100", "channels": "0"}),
    ("a-volume", "fx_a.mp3", {"gain": 6, "loudnorm": False, "format": "mp3", "bitrate": 128}),
    ("a-cut", "fx_a.mp3", {"start": 0.5, "duration": 1, "format": "mp3", "bitrate": 192}),
    ("convert", "fx_v.mp4", {"format": "mkv", "mode": "copy"}),
    ("convert", "fx_v.mp4", {"format": "jpg", "at": 0.5, "quality": 90}),
    ("convert", "fx_p.png", {"format": "jpg", "quality": 80}),
    ("convert", "fx_a.mp3", {"format": "wav", "bitrate": 192}),
]


def main():
    print("фикстуры:", ", ".join(make_fixtures()))
    fails = 0
    for i, (op, fixture, opts) in enumerate(CASES):
        try:
            meta = upload(fixture)
            done = run(op, meta["token"], opts)
            ok = done["stage"] == "done" and done["output"]
            print(("PASS" if ok else "FAIL"), "%2d %-10s %-9s -> %s (%s)"
                  % (i, op, fixture, done["output"], done["stage"]))
            if not ok:
                print("     error:", done.get("error"))
                fails += 1
        except Exception as e:  # noqa: BLE001
            print("FAIL %2d %-10s %-9s EXC %s" % (i, op, fixture, e))
            fails += 1

    # v-mux требует два файла — отдельный случай с token2
    try:
        meta = upload("fx_v.mp4")
        meta2 = upload("fx_a.mp3")
        done = run("v-mux", meta["token"],
                   {"container": "mp4", "audio": "aac", "audio_bitrate": 96}, meta2["token"])
        ok = done["stage"] == "done" and done["output"]
        print(("PASS" if ok else "FAIL"), "  %-10s %-9s -> %s (%s)" % ("v-mux", "2 файла",
              done["output"], done["stage"]))
        if not ok:
            print("     error:", done.get("error"))
            fails += 1
    except Exception as e:  # noqa: BLE001
        print("FAIL   v-mux      2 файла  EXC %s" % e)
        fails += 1

    print("FAIL count:", fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
