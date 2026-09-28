"""Проверка API после правок: группы вкладок, схемы, отсутствие clientlog."""
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8756"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


tabs = get("/api/tabs")
groups = tabs["groups"]
print("группы:", [g["title"] for g in groups])
counts = {}
for t in tabs["tabs"]:
    counts.setdefault(t["group"], []).append(t["title"])
for g in groups:
    print("  %-8s %d  %s" % (g["id"], len(counts.get(g["id"], [])), ", ".join(counts.get(g["id"], []))))
print("всего вкладок:", len(tabs["tabs"]))
print("все ready:", all(t["ready"] for t in tabs["tabs"]))

ops = get("/api/media/ops")["ops"]
print("медиа-операций:", len(ops), sorted(o["id"] for o in ops))
mux = [o for o in ops if o["id"] == "v-mux"][0]
print("v-mux files:", mux["files"], "accept2:", mux["accept2"])

try:
    get("/api/clientlog")
    print("ОШИБКА: /api/clientlog всё ещё отвечает")
except urllib.error.HTTPError as exc:
    print("clientlog удалён:", exc.code)

health = get("/api/health")
print("ffmpeg:", health["ffmpeg_short"], "| GPU:", health["devices"]["gpu"]["available"],
      "| ядер:", health["cores"], "| моделей МБ:", health["models_mb"])
