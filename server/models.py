"""Реестр моделей и их докачка с прогрессом.

У двух движков разные соглашения об именах, это важно и легко перепутать:

  realesrgan-ncnn-vulkan (GPU): -m <каталог> -n <имя>, читает
      <каталог>/<имя>.param + <имя>.bin
  realsr-ncnn-vulkan (CPU): -m <каталог> -s 4, читает
      <каталог>/x4.param + x4.bin, и имя каталога обязано содержать
      «models-DF2K» или «models-DF2K_JPEG» — иначе бинарь печатает
      «unknown model dir type» и выходит.

Поэтому у каждой модели свой каталог и своё имя файла для каждого движка.
"""
from __future__ import annotations

import os
import shutil
import threading
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

import config

HF = "https://huggingface.co/tumuyan2/realsr-models/resolve/main/"

# Каталоги, которые бинарь realsr-ncnn-vulkan принимает как «свои».
CPU_DF2K = "models-DF2K"
CPU_DF2K_JPEG = "models-DF2K_JPEG"

MODELS = [
    {
        "id": "realesr-general-x4v3",
        "title": "RealESRGAN general v3",
        "summary": "Сбалансированная модель для общих сцен: люди, пейзажи, интерьеры.",
        "scale": 4,
        "engines": ["gpu"],
        "size_mb": 2.3,
        "default": True,
        "files": {
            "param": [HF + "models-RealeSR-general-v3/x4.param"],
            "bin": [HF + "models-RealeSR-general-v3/x4.bin"],
        },
    },
    {
        "id": "AnimeSharp",
        "title": "AnimeSharp ×4",
        "summary": "Аниме с усиленными штрихами: линии остаются резкими, детали не мылятся.",
        "scale": 4,
        "engines": ["gpu"],
        "size_mb": 63.9,
        "files": {
            "param": ["https://huggingface.co/Kim2091/AnimeSharp/resolve/main/NCNN/4x-AnimeSharp-fp32.param"],
            "bin": ["https://huggingface.co/Kim2091/AnimeSharp/resolve/main/NCNN/4x-AnimeSharp-fp32.bin"],
        },
    },
    {
        "id": "srmd-x2",
        "title": "SRMD ×2 (люди, натив)",
        "summary": "Нативный ×2 для реальных фото и людей. Лёгкая сеть — самый быстрый "
                   "нейросетевой вариант. Хорошо держит сжатое видео.",
        "scale": 2,
        "engines": ["srmd"],
        "size_mb": 2.9,
        "dest": "srmd_x2",
        "noise": -1,
        "files": {
            "param": [HF + "models-srmd/srmd_x2.param"],
            "bin": [HF + "models-srmd/srmd_x2.bin"],
            "local": [("models-srmd/srmd_x2", "srmd_x2.param"), ("models-srmd/srmd_x2", "srmd_x2.bin")]
        },
    },
    {
        "id": "srmd-x3",
        "title": "SRMD ×3 (люди, натив)",
        "summary": "Нативный ×3 для реальных фото. Без даунскейла и апскейла-довеска.",
        "scale": 3,
        "engines": ["srmd"],
        "size_mb": 2.9,
        "dest": "srmd_x3",
        "noise": -1,
        "files": {
            "param": [HF + "models-srmd/srmd_x3.param"],
            "bin": [HF + "models-srmd/srmd_x3.bin"],
            "local": [("models-srmd/srmd_x3", "srmd_x3.param"), ("models-srmd/srmd_x3", "srmd_x3.bin")]
        },
    },
    {
        "id": "srmd-x4",
        "title": "SRMD ×4 (люди, натив)",
        "summary": "Нативный ×4 для реальных фото. Легче и быстрее RealESRGAN, мягче детали.",
        "scale": 4,
        "engines": ["srmd"],
        "size_mb": 3.0,
        "dest": "srmd_x4",
        "noise": -1,
        "files": {
            "param": [HF + "models-srmd/srmd_x4.param"],
            "bin": [HF + "models-srmd/srmd_x4.bin"],
            "local": [("models-srmd/srmd_x4", "srmd_x4.param"), ("models-srmd/srmd_x4", "srmd_x4.bin")]
        },
    },
    {
        "id": "srmdnf-x2",
        "title": "SRMD-денойз ×2 (люди, натив)",
        "summary": "Тот же SRMD ×2, но с подавлением шума — для зернистых и старых записей.",
        "scale": 2,
        "engines": ["srmd"],
        "size_mb": 2.9,
        "dest": "srmdnf_x2",
        "noise": 3,
        "files": {
            "param": [HF + "models-srmd/srmdnf_x2.param"],
            "bin": [HF + "models-srmd/srmdnf_x2.bin"],
            "local": [("models-srmd/srmdnf_x2", "srmdnf_x2.param"), ("models-srmd/srmdnf_x2", "srmdnf_x2.bin")]
        },
    },
    {
        "id": "srmdnf-x3",
        "title": "SRMD-денойз ×3 (люди, натив)",
        "summary": "SRMD ×3 с подавлением шума.",
        "scale": 3,
        "engines": ["srmd"],
        "size_mb": 2.9,
        "dest": "srmdnf_x3",
        "noise": 3,
        "files": {
            "param": [HF + "models-srmd/srmdnf_x3.param"],
            "bin": [HF + "models-srmd/srmdnf_x3.bin"],
            "local": [("models-srmd/srmdnf_x3", "srmdnf_x3.param"), ("models-srmd/srmdnf_x3", "srmdnf_x3.bin")]
        },
    },
    {
        "id": "srmdnf-x4",
        "title": "SRMD-денойз ×4 (люди, натив)",
        "summary": "SRMD ×4 с подавлением шума.",
        "scale": 4,
        "engines": ["srmd"],
        "size_mb": 3.0,
        "dest": "srmdnf_x4",
        "noise": 3,
        "files": {
            "param": [HF + "models-srmd/srmdnf_x4.param"],
            "bin": [HF + "models-srmd/srmdnf_x4.bin"],
            "local": [("models-srmd/srmdnf_x4", "srmdnf_x4.param"), ("models-srmd/srmdnf_x4", "srmdnf_x4.bin")]
        },
    },
    {
        "id": "realesrgan-x4plus",
        "title": "RealESRGAN x4plus",
        "summary": "Классическая модель качества: детализация текстур, фотореализм.",
        "scale": 4,
        "engines": ["gpu"],
        "size_mb": 32.0,
        # Входит в portable-архив, скачивать не нужно.
        "files": {
            "param": [],
            "bin": [],
            "local": [("realesrgan-x4plus", "{}.param"), ("realesrgan-x4plus", "{}.bin")],
        },
    },
    {
        "id": "realesrgan-x4plus-anime",
        "title": "RealESRGAN x4plus anime",
        "summary": "Аниме-иллюстрация: чистые линии, аккуратные японские надписи.",
        "scale": 4,
        "engines": ["gpu"],
        "size_mb": 8.6,
        "files": {
            "param": [],
            "bin": [],
            "local": [("realesrgan-x4plus-anime", "{}.param"), ("realesrgan-x4plus-anime", "{}.bin")],
        },
    },
    {
        "id": "realesr-animevideov3-x2",
        "title": "RealESRGAN animevideo v3 ×2",
        "summary": "Аниме-видео, лёгкая модель. Двойное увеличение без лишней детализации.",
        "scale": 2,
        "engines": ["gpu"],
        "size_mb": 1.2,
        "files": {
            "param": [],
            "bin": [],
            "local": [("realesr-animevideov3-x2", "{}.param"), ("realesr-animevideov3-x2", "{}.bin")],
        },
    },
    {
        "id": "realesr-animevideov3-x3",
        "title": "RealESRGAN animevideo v3 ×3",
        "summary": "Аниме-видео, тройное увеличение. Баланс скорости и деталей.",
        "scale": 3,
        "engines": ["gpu"],
        "size_mb": 1.2,
        "files": {
            "param": [],
            "bin": [],
            "local": [("realesr-animevideov3-x3", "{}.param"), ("realesr-animevideov3-x3", "{}.bin")],
        },
    },
    {
        "id": "realesr-animevideov3-x4",
        "title": "RealESRGAN animevideo v3 ×4",
        "summary": "Аниме-видео, четверное увеличение. Самая лёгкая из animevideo.",
        "scale": 4,
        "engines": ["gpu"],
        "size_mb": 1.2,
        "files": {
            "param": [],
            "bin": [],
            "local": [("realesr-animevideov3-x4", "{}.param"), ("realesr-animevideov3-x4", "{}.bin")],
        },
    },
    {
        "id": "DF2K",
        "title": "ESRGAN DF2K (CPU)",
        "summary": "Модель, на которой realsr-ncnn-vulkan умеет считать на процессоре. "
                   "Ровнее, но слабее на мелких деталях.",
        "scale": 4,
        "engines": ["cpu"],
        "size_mb": 32.0,
        "files": {
            "param": [],
            "bin": [],
            "local": [("models-DF2K/x4", "x4.param"), ("models-DF2K/x4", "x4.bin")],
        },
    },
    {
        "id": "DF2K_JPEG",
        "title": "ESRGAN DF2K JPEG (CPU)",
        "summary": "DF2K, обученная на JPEG-артефактах. Чище на сильно сжатом видео.",
        "scale": 4,
        "engines": ["cpu"],
        "size_mb": 32.0,
        "files": {
            "param": [],
            "bin": [],
            "local": [("models-DF2K_JPEG/x4", "x4.param"), ("models-DF2K_JPEG/x4", "x4.bin")],
        },
    },
]

MODELS_BY_ID = {m["id"]: m for m in MODELS}

_lock = threading.Lock()
_progress: dict[str, dict] = {}


# --- раскладка файлов по движкам ---------------------------------------

def gpu_dir() -> Path:
    path = config.GPU_DIR / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cpu_dir(name: str = CPU_DF2K) -> Path:
    path = config.CPU_DIR / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def srmd_dir() -> Path:
    path = config.SRMD_DIR / "models-srmd"
    path.mkdir(parents=True, exist_ok=True)
    return path


def install_paths(model: dict, engine: str) -> dict:
    """Куда класть param/bin для конкретного движка."""
    if engine == "cpu":
        folder = CPU_DF2K if model["id"] == "DF2K" else CPU_DF2K_JPEG
        base = cpu_dir(folder)
        return {"param": base / "x4.param", "bin": base / "x4.bin", "dir": base}
    if engine == "srmd":
        # srmd-ncnn-vulkan ищет файлы строго по именам srmd_x2.param и т.п.
        dest = model.get("dest") or model["id"].replace("-", "_")
        base = srmd_dir()
        return {
            "param": base / "{}.param".format(dest),
            "bin": base / "{}.bin".format(dest),
            "dir": base,
        }
    base = gpu_dir()
    return {
        "param": base / "{}.param".format(model["id"]),
        "bin": base / "{}.bin".format(model["id"]),
        "dir": base,
    }


def model_dir(engine: str) -> Path:
    """Каталог, который передаётся в -m."""
    if engine == "srmd":
        return srmd_dir()
    return gpu_dir() if engine == "gpu" else cpu_dir()


def is_installed(model: dict, engine: Optional[str] = None) -> bool:
    engines = model["engines"] if engine is None else [engine]
    for eng in engines:
        paths = install_paths(model, eng)
        if not (paths["param"].exists() and paths["bin"].exists()):
            return False
        if paths["param"].stat().st_size < 512 or paths["bin"].stat().st_size < 1024:
            return False
    return True


def installed_size_mb(model: dict) -> float:
    total = 0
    for engine in model["engines"]:
        paths = install_paths(model, engine)
        for key in ("param", "bin"):
            if paths[key].exists():
                total += paths[key].stat().st_size
    return round(total / 1048576, 2)


def list_models(engine: Optional[str] = None) -> list[dict]:
    out = []
    for model in MODELS:
        if engine and engine not in model["engines"]:
            continue
        item = dict(model)
        item.pop("files", None)
        item["installed"] = is_installed(model, engine)
        item["installed_mb"] = installed_size_mb(model)
        item["progress"] = get_progress(model["id"])
        out.append(item)
    return out


# --- прогресс ----------------------------------------------------------

def get_progress(model_id: str) -> Optional[dict]:
    with _lock:
        data = _progress.get(model_id)
        return dict(data) if data else None


def _set_progress(model_id: str, **kwargs) -> None:
    with _lock:
        current = _progress.setdefault(model_id, {"done": 0, "total": 0, "state": "idle"})
        current.update(kwargs)
        current["percent"] = (
            int(current["done"] * 100 / current["total"]) if current.get("total") else 0
        )


# --- установка ---------------------------------------------------------

def _local_candidates(model: dict, engine: str, filename: str) -> list[Path]:
    """Где файл уже может лежать: в архиве движка или в zip-файле рядом."""
    roots = [config.ENGINES[engine]["dir"]] if engine in config.ENGINES else []
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for name in (filename, filename.replace(".param", ".bin")):
            direct = root / name
            if direct.exists() and direct.name == name:
                found.append(direct)
        for zip_path in root.glob("*.zip"):
            try:
                with zipfile.ZipFile(zip_path) as zf:
                    for entry in zf.namelist():
                        entry = entry.replace("\\", "/")
                        if entry.endswith("/" + filename) or entry == filename:
                            found.append(root / ("__fromzip__" + filename))
                            with open(root / ("__fromzip__" + filename), "wb") as fh:
                                fh.write(zf.read(entry))
            except (OSError, zipfile.BadZipFile):
                continue
    return found


def _download(url: str, dst: Path, model_id: str, label: str) -> bool:
    tmp = dst.with_suffix(dst.suffix + ".part")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PedroDevTools"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            _set_progress(model_id, state="downloading", file=label, total=total, done=0)
            done = 0
            with open(tmp, "wb") as fh:
                while True:
                    block = resp.read(262144)
                    if not block:
                        break
                    fh.write(block)
                    done += len(block)
                    _set_progress(model_id, done=done, total=total or done)
        tmp.replace(dst)
        return True
    except (OSError, ValueError) as exc:
        tmp.unlink(missing_ok=True)
        _set_progress(model_id, state="error", error=str(exc))
        return False


def _place_from_local(model: dict, engine: str, dst: Path) -> bool:
    files = model.get("files") or {}
    local = files.get("local") or []
    wanted = dst.name
    ext = dst.suffix
    for _, filename in local:
        if not filename.endswith(wanted):
            continue
        for candidate in _local_candidates(model, engine, filename):
            if candidate.exists() and candidate.stat().st_size > 0:
                dst.parent.mkdir(parents=True, exist_ok=True)
                if candidate.resolve() != dst.resolve():
                    shutil.copyfile(candidate, dst)
                return True
    return False


def install(model_id: str, on_log: Optional[Callable[[str], None]] = None) -> dict:
    model = MODELS_BY_ID.get(model_id)
    if not model:
        raise KeyError("Неизвестная модель: " + model_id)
    engine = model["engines"][0]
    log = on_log or (lambda _: None)

    if is_installed(model, engine):
        _set_progress(model_id, state="done", done=1, total=1, percent=100)
        return {"model": model_id, "state": "done", "cached": True}

    _set_progress(model_id, state="working", done=0, total=0, percent=0, error="")
    log("Установка модели «{}»".format(model["title"]))

    files = model.get("files") or {}
    for key in ("param", "bin"):
        paths = install_paths(model, engine)
        dst = paths[key]
        if dst.exists() and dst.stat().st_size > 0:
            continue
        if _place_from_local(model, engine, dst):
            log("{} взят из локального архива".format(key))
            continue
        urls = files.get(key) or []
        if not urls:
            _set_progress(model_id, state="error", error="Нет источника для " + key)
            raise RuntimeError("Нет источника для файла модели: " + key)
        ok = False
        for url in urls:
            log("Скачивание {} ({})".format(key, url.rsplit("/", 1)[-1]))
            if _download(url, dst, model_id, key):
                ok = True
                break
        if not ok:
            _set_progress(model_id, state="error", error="Не удалось скачать " + key)
            raise RuntimeError("Не удалось скачать файл модели: " + key)

    if not is_installed(model, engine):
        _set_progress(model_id, state="error", error="Файлы модели неполные")
        raise RuntimeError("Файлы модели неполные")

    _post_install_fixups(model, engine, log)

    log("Модель «{}» готова".format(model["title"]))
    _set_progress(model_id, state="done", done=1, total=1, percent=100)
    return {"model": model_id, "state": "done", "size_mb": installed_size_mb(model)}


def _post_install_fixups(model: dict, engine: str, log: Callable[[str], None]) -> None:
    """Доводка файлов после скачивания. Сейчас заглушка: бывшая здесь правка
    input→data для сторонней конверсии x2plus убрана вместе с самой моделью —
    её .param использовал слой MemoryData, которого нет в ncnn 2022 года
    (ошибка «layer MemoryData not exists or registered»)."""
    return


def remove(model_id: str) -> dict:
    model = MODELS_BY_ID.get(model_id)
    if not model:
        raise KeyError("Неизвестная модель: " + model_id)
    freed = 0
    for engine in model["engines"]:
        paths = install_paths(model, engine)
        for key in ("param", "bin"):
            if paths[key].exists():
                freed += paths[key].stat().st_size
                paths[key].unlink()
    with _lock:
        _progress.pop(model_id, None)
    return {"model": model_id, "state": "removed", "freed_mb": round(freed / 1048576, 2)}


def models_size_mb() -> float:
    total = 0
    for path in config.BIN_DIR.rglob("*"):
        if path.is_file() and "__fromzip__" not in path.name:
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return round(total / 1048576, 1)


def cleanup_temp() -> None:
    """Убирает файлы, распакованные нами из zip-архивов движков."""
    for engine in config.ENGINES.values():
        root = engine["dir"]
        if not root.exists():
            continue
        for path in root.glob("__fromzip__*"):
            try:
                path.unlink()
            except OSError:
                pass
