"""Апскейл одного фото нейросетью. Без чанков: один вызов движка + доводка."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

import config
import ffmpeg
import jobs
import media
import models
import upscale
from jobs import Job

PERCENT_RE = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*%")

IMAGE_OUT = {
    "jpg": ("jpg", "photo"),
    "png": ("png", "photo"),
    "webp": ("webp", "photo"),
}


def upscale_image(job: Job, src: Path, opts: dict, meta: dict) -> Path:
    engine = upscale.resolve_engine(opts)
    model = upscale.pick_model(opts, engine)
    m_engine = model["engines"][0]

    if not upscale.models_engine_ok(m_engine):
        raise RuntimeError("Движок {} не установлен".format(m_engine))
    if not model["installed"]:
        job.set_stage("preparing", "скачивание модели")
        models.install(model["id"], job.log)

    target = upscale.compute_target(meta, opts, model.get("scale"))
    scales = upscale.available_scales(model)
    job.set_stage("preparing")
    job.log("Движок: {} | модель: {} | {}×{} → {}×{}".format(
        {"gpu": "GPU/Vulkan", "cpu": "CPU", "srmd": "SRMD/Vulkan"}.get(m_engine, m_engine),
        model["title"], meta["width"], meta["height"],
        target["target_w"], target["target_h"],
    ))
    job.log("Модель умеет {} → движок получит -s {}".format(
        ", ".join("×{}".format(s) for s in scales), target["model_scale"]
    ))

    work = config.TMP_DIR / "img_{}".format(job.id)
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    job.tmp_dir = work

    exe = config.ENGINES[m_engine]["exe"]
    if m_engine == "gpu":
        models_dir = models.model_dir(m_engine)
    else:
        models_dir = models.install_paths(model, m_engine)["dir"]
    gpu_id = -1 if m_engine == "cpu" else int(opts.get("gpu_id", 0) or 0)
    tile = int(opts.get("tile") or 0)

    tmp_up = work / "up.png"
    cmd = [
        str(exe),
        "-i", str(src),
        "-o", str(tmp_up),
        "-s", str(target["model_scale"]),
        "-m", str(models_dir),
        "-g", str(gpu_id),
        "-f", "png",
    ]
    if m_engine == "gpu":
        cmd += ["-n", model["id"]]
    elif m_engine == "srmd":
        cmd += ["-n", str(int(model.get("noise", -1)))]
    if tile > 0:
        cmd += ["-t", str(tile)]
    if opts.get("tta"):
        cmd += ["-x"]

    job.set_stage("upscaling", "апскейл")
    job.percent = 15.0
    _run_single(cmd, job, m_engine)
    job.check_cancel()
    if not tmp_up.exists():
        raise RuntimeError("Движок не создал файл")

    fmt = (opts.get("format") or "keep").lower()
    if fmt in ("keep", "jpeg"):
        fmt = src.suffix.lower().lstrip(".") or "jpg"
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt not in ("jpg", "png", "webp"):
        fmt = "png"

    stem = Path(job.original_name or src.name).stem or "photo"
    final = config.OUT_DIR / "{}_up{}.{}".format(stem, "{:g}".format(target["exact_factor"]), fmt)
    if final.exists():
        final = config.OUT_DIR / "{}_up{}_{}.{}".format(
            stem, "{:g}".format(target["exact_factor"]), job.id[:6], fmt)

    job.set_stage("encoding", "сохранение")
    job.percent = 85.0
    cmd2 = [ffmpeg.find_binary("ffmpeg"), "-y", "-v", "error", "-i", str(tmp_up)]
    if target["needs_resize"]:
        cmd2 += ["-vf", "scale={}:{}:flags=lanczos".format(target["target_w"], target["target_h"])]
        job.log("Доводка ресайзом до {}×{}".format(target["target_w"], target["target_h"]))
    cmd2 += ["-frames:v", "1"] + media.photo_quality_args(fmt, int(opts.get("quality", 90))) + [str(final)]
    if ffmpeg.run(cmd2).returncode != 0:
        raise RuntimeError("Не смог сохранить результат")

    try:
        job.output_meta = ffmpeg.probe_any(final)
    except RuntimeError:
        job.output_meta = {}
    job.log("Готово: {} ({:.1f} МБ)".format(final.name, final.stat().st_size / 1048576))
    return final


def _run_single(cmd: list[str], job: Job, engine: str) -> None:
    """Один кадр: проценты честно парсим из stdout (там «12,34%» на тайл)."""
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    workdir = config.ENGINES[engine]["dir"]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        startupinfo=startupinfo,
        creationflags=ffmpeg.CREATE_NO_WINDOW,
        cwd=str(workdir),
    )
    shown = -1.0
    try:
        for line in proc.stdout:
            job.check_cancel()
            text = line.rstrip()
            if not text or any(
                k in text for k in ("queueC", "bugsbn", "fp16", "subgroup", "device: ", "_wfopen")
            ):
                continue
            match = PERCENT_RE.search(text)
            if match:
                try:
                    pct = float(match.group(1).replace(",", "."))
                except ValueError:
                    continue
                job.percent = 15.0 + min(99.0, max(0.0, pct)) * 0.65
                if pct - shown >= 20.0:
                    shown = pct
                    job.log("апскейл: {:.0f}%".format(pct))
                continue
            job.log(text)
    finally:
        if job.cancelled:
            ffmpeg.terminate(proc)
    code = proc.wait()
    job.percent = 80.0
    if code != 0:
        raise RuntimeError("Движок апскейла завершился с кодом {}".format(code))
