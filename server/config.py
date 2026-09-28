"""Пути, настройки и состояние PedroDevTools."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BIN_DIR = ROOT / "bin"
WEB_DIR = ROOT / "web"
TMP_DIR = ROOT / "tmp"
OUT_DIR = ROOT / "output"
PRESETS_DIR = ROOT / "presets"
SETTINGS_FILE = PRESETS_DIR / "settings.json"
USER_PRESETS_FILE = PRESETS_DIR / "presets.json"

GPU_DIR = BIN_DIR / "realesrgan"
CPU_DIR = BIN_DIR / "realsr" / "realsr-ncnn-vulkan-20220728-windows"
SRMD_DIR = BIN_DIR / "srmd" / "srmd-ncnn-vulkan-20220728-windows"

GPU_EXE = GPU_DIR / "realesrgan-ncnn-vulkan.exe"
CPU_EXE = CPU_DIR / "realsr-ncnn-vulkan.exe"
SRMD_EXE = SRMD_DIR / "srmd-ncnn-vulkan.exe"

HOST = "127.0.0.1"
PORT = 8756

# Реестр движков. cpu — realsr-ncnn-vulkan, единственный, кто умеет -g -1.
ENGINES = {
    "gpu": {
        "title": "Real-ESRGAN (Vulkan)",
        "exe": GPU_EXE,
        "dir": GPU_DIR,
        "models_dir": GPU_DIR / "models",
        "cpu": False,
    },
    "cpu": {
        "title": "RealSR (CPU)",
        "exe": CPU_EXE,
        "dir": CPU_DIR,
        "models_dir": CPU_DIR / "models-DF2K",
        "cpu": True,
    },
    "srmd": {
        "title": "SRMD (Vulkan, быстрый)",
        "exe": SRMD_EXE,
        "dir": SRMD_DIR,
        "models_dir": SRMD_DIR / "models-srmd",
        "cpu": False,
    },
}

DEFAULT_SETTINGS = {
    "engine": "auto",
    "model": "realesr-general-x4v3",
    "scale_mode": "percent",
    "percent": 200,
    "width": 1920,
    "height": 1080,
    "keep_width": False,
    "keep_height": True,
    "long_edge": 1920,
    "even_only": True,
    "tile": 0,
    "threads": 4,
    "tta": False,
    "chunk_seconds": 10,
    "container": "mp4",
    "codec": "libx264",
    "crf": 17,
    "crf_mode": "crf",
    "bitrate": 8000,
    "x264_preset": "slow",
    "fps": 0,
    "audio": "copy",
    "audio_bitrate": 192,
    "start": 0,
    "duration": 0,
}

_lock = threading.Lock()


def ensure_dirs() -> None:
    for path in (BIN_DIR, TMP_DIR, OUT_DIR, PRESETS_DIR, GPU_DIR, CPU_DIR, SRMD_DIR):
        path.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict:
    data = dict(DEFAULT_SETTINGS)
    if SETTINGS_FILE.exists():
        try:
            stored = json.loads(SETTINGS_FILE.read_text("utf-8"))
            if isinstance(stored, dict):
                data.update({k: v for k, v in stored.items() if k in DEFAULT_SETTINGS})
        except (OSError, ValueError):
            pass
    return data


def save_settings(values: dict) -> dict:
    with _lock:
        current = load_settings()
        for key in DEFAULT_SETTINGS:
            if key in values:
                current[key] = values[key]
        PRESETS_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(
            json.dumps(current, ensure_ascii=False, indent=2), "utf-8"
        )
    return current


def load_user_presets() -> list[dict]:
    if not USER_PRESETS_FILE.exists():
        return []
    try:
        data = json.loads(USER_PRESETS_FILE.read_text("utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_user_presets(items: list[dict]) -> list[dict]:
    with _lock:
        PRESETS_DIR.mkdir(parents=True, exist_ok=True)
        USER_PRESETS_FILE.write_text(
            json.dumps(items, ensure_ascii=False, indent=2), "utf-8"
        )
    return items


def free_bytes(path: Path) -> int:
    try:
        return os.statvfs(path).f_bavail * os.statvfs(path).f_frsize if os.name != "nt" else _free_windows(path)
    except (OSError, AttributeError):
        return 0


def _free_windows(path: Path) -> int:
    import ctypes

    free = ctypes.c_ulonglong(0)
    ctypes.windll.kernel32.GetDiskFreeSpaceExW(
        ctypes.c_wchar_p(str(path)), ctypes.byref(free), None, None
    )
    return int(free.value)
