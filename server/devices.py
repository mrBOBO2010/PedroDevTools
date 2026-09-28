"""Проба вычислительных устройств.

ctypes-обвязка Vulkan оказалась ненадёжной (в этом окружении вызов
vkGetInstanceProcAddr падает с access violation), поэтому список устройств берётся
из реального вывода самих движков: и это честнее — показывает ровно то, с чем
реально работает бинарь, а не абстрактный Vulkan.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Optional

import config

CREATE_NO_WINDOW = 0x08000000

# Строки вида «[0 Radeon RX 550X]  queueC=1[2] ...» из вывода ncnn.
DEVICE_RE = re.compile(r"^\[(\d+)\s+([^\]]+)\]", re.MULTILINE)
API_RE = re.compile(r"Vulkan API Version\s+([\d.]+)", re.IGNORECASE)
FP16_RE = re.compile(r"fp16-p/s/a=(\d)", re.IGNORECASE)

_cache: dict = {"key": None, "value": None}
_lock = threading.Lock()


def _run(cmd: list[str], cwd: Path, timeout: int = 60) -> tuple[int, str]:
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=startupinfo,
            creationflags=CREATE_NO_WINDOW,
            cwd=str(cwd),
        )
    except OSError:
        return -1, ""
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        return -1, out or ""
    return proc.returncode, out or ""


def _probe_image() -> Optional[Path]:
    """Крошечная картинка, чтобы движок успел напечатать список устройств."""
    import ffmpeg

    exe = ffmpeg.find_binary("ffmpeg")
    if not exe:
        return None
    path = config.TMP_DIR / "device_probe.png"
    try:
        config.TMP_DIR.mkdir(parents=True, exist_ok=True)
        ffmpeg.run([exe, "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:size=16x16",
                    "-frames:v", "1", "-f", "image2", str(path)])
    except Exception:  # noqa: BLE001
        return None
    return path if path.exists() else None


def list_gpu_devices() -> list[dict]:
    """Устройства, которые видит realesный бинарь апскейлера."""
    exe = config.GPU_EXE
    if not exe.exists():
        return []
    probe = _probe_image()
    if not probe:
        return []

    code, out = _run([
        str(exe), "-i", str(probe), "-o", str(config.TMP_DIR / "device_probe_out.png"),
        "-g", "-1", "-s", "2",
    ], cwd=exe.parent, timeout=60)

    # ncnn печатает по несколько строк на одно устройство («queueC», «bugsbn…»,
    # «fp16-p/s/a», «subgroup»), поэтому собираем уникальные по индексу.
    devices: dict[int, dict] = {}
    for match in DEVICE_RE.finditer(out):
        index = int(match.group(1))
        if index in devices:
            continue
        block = out[match.start(): match.start() + 200]
        fp16 = FP16_RE.search(block)
        devices[index] = {
            "id": index,
            "name": match.group(2).strip(),
            "type": "графическая",
            "fp16": bool(fp16 and fp16.group(1) == "1"),
            "api": "",
        }
    return list(devices.values())


def cpu_device() -> dict:
    return {
        "id": -1,
        "name": "Процессор (CPU)",
        "type": "программный",
        "fp16": False,
        "api": "",
        "cpu": True,
    }


def logical_cores() -> int:
    try:
        return os.cpu_count() or 1
    except Exception:  # noqa: BLE001
        return 1


def probe(refresh: bool = False) -> dict:
    """Полная картина устройств и движков для /api/health."""
    key = (config.GPU_EXE.exists(), config.CPU_EXE.exists())
    with _lock:
        if not refresh and _cache["key"] == key and _cache["value"]:
            cached = dict(_cache["value"])
            cached["logical_cores"] = logical_cores()
            return cached

    gpus = list_gpu_devices()
    gpu_ready = bool(gpus) and config.GPU_EXE.exists()

    note = ""
    if not config.GPU_EXE.exists():
        note = "Бинарь realesргана не установлен"
    elif not gpus:
        note = "Бинарь не видит ни одного Vulkan-устройства — проверьте драйвер видеокарты"

    cpu_ready = config.CPU_EXE.exists()
    result = {
        "gpus": gpus,
        "cpu": cpu_device(),
        "gpu": {
            "available": gpu_ready,
            "note": note,
            "engine": "realesrgan-ncnn-vulkan",
            "exe": str(config.GPU_EXE),
        },
        "cpus": {
            "available": cpu_ready,
            "note": "" if cpu_ready else "Бинарь realsr-ncnn-vulkan не установлен",
            "engine": "realsr-ncnn-vulkan",
            "exe": str(config.CPU_EXE),
        },
        "logical_cores": logical_cores(),
    }

    with _lock:
        _cache["key"] = key
        _cache["value"] = result
    return result


def invalidate() -> None:
    with _lock:
        _cache["key"] = None
        _cache["value"] = None
