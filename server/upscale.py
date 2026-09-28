"""Чанковый пайплайн апскейла.

Порядок: ffprobe -> чанки кадров -> realesрган (по чанкам) -> кодирование чанка ->
concat -> mux аудио. Промежуточные кадры каждого чанка удаляются сразу, поэтому
расход диска не растёт с длиной ролика.
"""
from __future__ import annotations

import math
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import config
import ffmpeg
import jobs
import models
from jobs import Job

PERCENT_RE = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*%")
FRAME_RE = re.compile(r"(\d+)\s*/\s*(\d+)")

# Контейнер, расширение, профиль кодирования по кодеку
CONTAINERS = {
    "mp4": {"ext": "mp4", "flags": ["-movflags", "+faststart"]},
    "mkv": {"ext": "mkv", "flags": []},
    "webm": {"ext": "webm", "flags": []},
}


def resolve_engine(options: dict) -> str:
    choice = options.get("engine", "auto")
    if choice in ("gpu", "cpu", "srmd"):
        return choice
    probe = config_devices_probe()
    if probe["gpu"]["available"] and models_engine_ok("gpu"):
        return "gpu"
    return "cpu"


def config_devices_probe() -> dict:
    import devices

    return devices.probe()


def models_engine_ok(engine: str) -> bool:
    engine_cfg = config.ENGINES.get(engine, {})
    exe = engine_cfg.get("exe")
    return bool(exe and exe.exists())


def models_for_engine(engine: str) -> list[dict]:
    return [m for m in models.list_models() if engine in m["engines"]]


def pick_model(options: dict, engine: str) -> dict:
    """Модель из настроек, если она годится для движка; иначе первая доступная."""
    candidates = models_for_engine(engine)
    if not candidates:
        raise RuntimeError("Нет моделей для движка " + engine)
    wanted = options.get("model")
    for item in candidates:
        if item["id"] == wanted:
            return item
    for item in candidates:
        if item.get("default"):
            return item
    return candidates[0]


def model_engine(model: dict) -> str:
    return model["engines"][0]


def compute_target(meta: dict, options: dict, model_scale: Optional[int] = None) -> dict:
    """Считает итоговое разрешение и масштаб модели.

    Модели ncnn умеют только целые ×2/×3/×4 (движок realsr — только ×4).
    Если пользователь просит 150%, берётся ближайший доступный апскейл и
    последующий ресайз через ffmpeg до точной цели. Масштаб никогда не берётся
    меньше единицы: если цель меньше исходника, кадр только уменьшается.
    """
    sw, sh = int(meta["width"]), int(meta["height"])
    mode = options.get("scale_mode", "percent")

    if mode == "percent":
        factor = float(options.get("percent", 200)) / 100.0
        target_w, target_h = sw * factor, sh * factor
    elif mode == "long_edge":
        edge = int(options.get("long_edge", 1920))
        if sw >= sh:
            target_w, target_h = edge, edge * sh / sw
        else:
            target_w, target_h = edge * sw / sh, edge
    else:
        keep_w = bool(options.get("keep_width"))
        keep_h = bool(options.get("keep_height"))
        want_w = int(options.get("width", sw))
        want_h = int(options.get("height", sh))
        if keep_width and not keep_h:
            target_w, target_h = want_w, want_w * sh / sw
        elif keep_height and not keep_w:
            target_w, target_h = want_h * sw / sh, want_h
        else:
            target_w, target_h = want_w, want_h

    target_w = max(2, int(round(target_w)))
    target_h = max(2, int(round(target_h)))
    if options.get("even_only", True):
        target_w -= target_w % 2
        target_h -= target_h % 2

    factor = max(1.0, max(target_w / sw, target_h / sh))
    scales = [int(model_scale)] if model_scale in (2, 3, 4) else None
    scale = pick_model_scale(factor, scales)
    intermediate = (sw * scale, sh * scale)
    exact = (intermediate[0] != target_w) or (intermediate[1] != target_h)

    return {
        "target_w": target_w,
        "target_h": target_h,
        "factor": round(factor, 4),
        "model_scale": scale,
        "intermediate": intermediate,
        "needs_resize": exact,
        "exact_factor": round(target_w / sw, 4),
    }


def available_scales(model: Optional[dict]) -> list[int]:
    """Масштабы, которые умеет конкретная модель. realsr умеет только ×4."""
    if model:
        declared = int(model.get("scale") or 4)
        if model.get("engines") and model["engines"][0] == "cpu":
            return [4] if declared == 4 else [declared]
        if declared in (2, 3, 4):
            return [declared]
    return [2, 3, 4]


def pick_model_scale(factor: float, scales: Optional[list[int]] = None) -> int:
    """Наименьший доступный апскейл, не меньший запрошенного, иначе минимальный.

    Без этого движок получал -s 2 при ×4-модели и падал с «invalid scale argument».
    """
    pool = sorted({int(s) for s in (scales or [2, 3, 4]) if int(s) in (2, 3, 4)}) or [4]
    for candidate in sorted(pool, reverse=True):
        if factor >= candidate - 1e-6:
            return candidate
    return min(pool)


def build_command(meta: dict, options: dict, engine: str, model: dict, target: dict, src: Path, dst: Path) -> list[str]:
    """Готовая CLI-строка для копирования в терминал."""
    profile = ffmpeg.CODEC_PROFILES.get(options.get("codec", "libx264"), ffmpeg.CODEC_PROFILES["libx264"])
    frames = target["intermediate"]
    vf = "scale={}:{}:flags=lanczos".format(frames[0], frames[1]) if target["needs_resize"] else "null"
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
    ]
    if options.get("start"):
        cmd += ["-ss", str(options["start"])]
    if options.get("duration"):
        cmd += ["-t", str(options["duration"])]
    cmd += ["-vf", vf]
    cmd += profile["args"](options)
    cmd += ["-pix_fmt", profile["pix"]]
    audio = options.get("audio", "copy")
    if audio == "copy":
        cmd += ["-c:a", "copy"]
    elif audio != "none":
        cmd += ["-c:a", "aac", "-b:a", str(options.get("audio_bitrate", 192)) + "k"]
    else:
        cmd += ["-an"]
    cmd += [str(dst)]
    return cmd





def _terminate(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
        except OSError:
            pass


def upscale(job: Job) -> Path:
    src = job.source
    opts = job.options
    meta = job.meta

    job.check_cancel()
    engine = resolve_engine(opts)
    model = pick_model(opts, engine)
    m_engine = model_engine(model)

    if not models_engine_ok(m_engine):
        raise RuntimeError("Движок {} не установлен".format(m_engine))
    if not model["installed"]:
        job.log("Модель {} отсутствует, устанавливаю".format(model["title"]))
        job.set_stage("preparing", "скачивание модели")
        models.install(model["id"], job.log)

    target = compute_target(meta, opts, model.get("scale"))
    scales = available_scales(model)
    job.set_stage("preparing")
    job.log("Движок: {} | модель: {} | {}→{}x{} (×{})".format(
        {"gpu": "GPU/Vulkan", "cpu": "CPU", "srmd": "SRMD/Vulkan"}.get(m_engine, m_engine),
        model["title"], meta["width"], target["target_w"], target["target_h"],
        target["factor"],
    ))
    job.log("Модель умеет {} → движок получит -s {}".format(
        ", ".join("×{}".format(s) for s in scales), target["model_scale"]
    ))
    if target["needs_resize"]:
        job.log("Добавлен ресайз до точного размера {}×{}".format(target["target_w"], target["target_h"]))

    src_w, src_h = int(meta["width"]), int(meta["height"])
    fps = float(meta.get("fps") or 0) or 25.0
    if opts.get("fps"):
        fps = float(opts["fps"])
    if fps <= 0:
        fps = 25.0

    start = float(opts.get("start") or 0)
    duration = float(opts.get("duration") or 0)
    if duration <= 0:
        duration = max(0.1, float(meta.get("duration") or 0) - start)
    chunk_seconds = max(1.0, float(opts.get("chunk_seconds") or 10))
    # ceil, а не round: round(13/10)=1 выкидывал хвост ролика молча.
    chunks = max(1, int(math.ceil(duration / chunk_seconds)))

    job.frames_total = int(round(fps * duration))
    job.set_progress(0, "preparing")

    work = config.TMP_DIR / "job_{}".format(job.id)
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    job.tmp_dir = work

    estimate = _estimate_chunk_bytes(src_w, src_h, target["intermediate"], fps, chunk_seconds)
    free = ffmpeg.free_space(config.TMP_DIR)
    if estimate > free * 0.5 and chunk_seconds > 1:
        new_chunk = max(1.0, chunk_seconds * 0.5)
        chunks = max(1, int(math.ceil(duration / new_chunk)))
        chunk_seconds = new_chunk
        job.log("Мало свободного места ({} МБ) — размер чанка уменьшен до {} с".format(
            free // 1024 // 1024, int(chunk_seconds)
        ))

    upscaled_dir = work / "upscaled"
    upscaled_dir.mkdir(parents=True, exist_ok=True)
    segments_dir = work / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)

    exe = config.ENGINES[m_engine]["exe"]
    # У CPU-моделей каталог с именем models-DF2K* — бинарь проверяет именно его.
    # У srmd файлы лежат прямо в models-srmd под своими именами.
    if m_engine == "gpu":
        models_dir = models.model_dir(m_engine)
    else:
        models_dir = models.install_paths(model, m_engine)["dir"]
    gpu_id = -1 if m_engine == "cpu" else int(opts.get("gpu_id", 0) or 0)

    tile = int(opts.get("tile") or 0)
    threads = max(1, int(opts.get("threads") or 1))
    load_proc_save = "{}:{}:{}".format(max(1, threads // 2), max(1, threads // 2), max(1, threads - threads // 2))

    frames_done = 0
    for index in range(chunks):
        job.check_cancel()
        chunk_start = start + index * chunk_seconds
        chunk_dur = min(chunk_seconds, start + duration - chunk_start)
        if chunk_dur <= 0.05:
            break
        chunk_frames = work / "in_{:02d}".format(index)
        out_frames = work / "out_{:02d}".format(index)

        job.set_stage("decoding", "декодирование чанка {}/{}".format(index + 1, chunks))
        extracted = ffmpeg.extract_frames(src, chunk_frames, chunk_start, chunk_dur, fps)
        if not extracted:
            job.log("Чанк {} пуст, пропускаю".format(index + 1))
            shutil.rmtree(chunk_frames, ignore_errors=True)
            continue
        job.log("Чанк {}/{}: {} кадров".format(index + 1, chunks, len(extracted)))

        job.set_stage("upscaling", "апскейл чанка {}/{}".format(index + 1, chunks))
        # Каталог вывода обязан существовать заранее: realsr-ncnn-vulkan проверяет
        # is_directory() по -o, и если каталога нет, пытается угадать формат из
        # расширения пути и падает с «invalid outputpath extension type».
        out_frames.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(exe),
            "-i", str(chunk_frames),
            "-o", str(out_frames),
            "-s", str(target["model_scale"]),
            "-m", str(models_dir),
            "-g", str(gpu_id),
            "-j", load_proc_save,
            "-f", "png",
        ]
        if m_engine == "gpu":
            cmd += ["-n", model["id"]]
        elif m_engine == "srmd":
            # У srmd -n это НЕ имя модели, а уровень шума: -1 = без денойза
            # (файлы srmd_x*), иначе берутся srmdnf_x*. Масштаб -s обязан
            # совпадать с файлами, иначе бинарь не найдёт веса.
            cmd += ["-n", str(int(model.get("noise", -1)))]
        if tile > 0:
            cmd += ["-t", str(tile)]
        if opts.get("tta"):
            cmd += ["-x"]

        _run_upscaler(cmd, job, frames_done, len(extracted), m_engine, out_frames)

        job.set_stage("encoding", "кодирование чанка {}/{}".format(index + 1, chunks))
        segment = segments_dir / "seg_{:04d}.mkv".format(index)
        # Если модель не дала точный размер — доводим его здесь, до кодирования.
        final_wh = (target["target_w"], target["target_h"]) if target["needs_resize"] else None
        ffmpeg.encode_chunk(out_frames, segment, opts, fps, final_wh)
        job.segments.append(segment)
        job.upscaled = [out_frames]

        frames_done += len(extracted)
        job.set_progress(frames_done)

        shutil.rmtree(chunk_frames, ignore_errors=True)
        shutil.rmtree(out_frames, ignore_errors=True)

    job.check_cancel()
    if not job.segments:
        raise RuntimeError("Не удалось обработать ни одного кадра")

    job.set_stage("assembling", "склейка чанков")
    job.log("Склейка {} чанков".format(len(job.segments)))

    container = CONTAINERS.get(opts.get("container", "mp4"), CONTAINERS["mp4"])
    codec = opts.get("codec", "libx264")
    forced_ext = ffmpeg.CODEC_PROFILES.get(codec, {}).get("ext", container["ext"])
    ext = forced_ext if opts.get("container", "auto") == "auto" else container["ext"]
    if ext == "webm" and codec not in ("libvpx-vp9",):
        ext = "mp4"

    silent = work / "video_silent.{}".format(ext)
    ffmpeg.concat_chunks(job.segments, silent)

    # Имя результата — от исходного имени файла, а не от временного токена.
    # Имя результата — от исходного имени файла, а не от временного токена.
    # Токен выглядит как 403e7edaf2.mp4 и в названии результата неуместен.
    stem = Path(job.original_name or src.name).stem or "video"
    if re.fullmatch(r"[0-9a-f]{8,12}", stem):
        stem = "video"
    # _up2x.mp4, а не _up2.0x.mp4 — точка в расширении ломает шаблон *.mp4.
    factor_tag = "{:g}".format(target["exact_factor"])
    final = config.OUT_DIR / Path("{}_up{}x.{}".format(stem, factor_tag, ext))
    if final.exists():
        stamp = time.strftime("%H%M%S")
        final = config.OUT_DIR / Path("{}_up{}x_{}.{}".format(stem, factor_tag, stamp, ext))

    job.set_stage("assembling", "сборка итогового файла")
    ffmpeg.mux_audio(silent, src, final, opts, start, duration)
    silent.unlink(missing_ok=True)

    try:
        job.output_meta = ffmpeg.probe(final)
    except RuntimeError:
        job.output_meta = {}

    # Страховка от молчаливой обрезки: результат обязан покрывать весь заказ.
    # Порог 0.9 с запасом на погрешности кодирования; оборванный хвост
    # (как было с round() вместо ceil()) даёт 0.77 и ниже — такое не отдаём.
    got_dur = float(job.output_meta.get("duration") or 0)
    if duration > 1.0 and got_dur > 0 and got_dur < duration * 0.9:
        raise RuntimeError(
            "Итоговый файл короче заказа: {:.1f} с вместо {:.1f} с — отдавать обрезок не буду".format(
                got_dur, duration
            )
        )

    job.log("Готово: {} ({:.1f} МБ, {:.1f} с)".format(
        final.name, final.stat().st_size / 1048576, got_dur
    ))
    return final


def _run_upscaler(
    cmd: list[str],
    job: Job,
    base_done: int,
    chunk_frames: int,
    engine: str,
    out_dir: Path,
) -> None:
    """Запуск движка с живым прогрессом.

    Проценты берём из числа готовых файлов в out_dir, а не из stdout: оба
    ncnn-движка печатают проценты через carriage return, из-за чего построчный
    разбор вёлся в ноль и прогрессбар стоял на 0 % до конца задачи.
    """
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

    lines: list[str] = []

    def drain() -> None:
        try:
            for line in proc.stdout:
                lines.append(line.rstrip())
        except (OSError, ValueError):
            pass

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()

    try:
        while proc.poll() is None:
            job.check_cancel()
            produced = _count_outputs(out_dir)
            job.set_progress(min(base_done + produced, base_done + chunk_frames), "upscaling")
            time.sleep(0.6)
    finally:
        if job.cancelled:
            _terminate(proc)

    reader.join(timeout=3)
    code = proc.wait()
    job.set_progress(base_done + _count_outputs(out_dir), "upscaling")

    percent_logged: Optional[float] = None
    for text in lines:
        if not text or any(
            k in text for k in ("queueC", "bugsbn", "fp16", "subgroup", "device: ", "_wfopen")
        ):
            continue
        # ncnn печатает «12,34%» на каждый кадр — в консоли это шум.
        match = PERCENT_RE.fullmatch(text.strip())
        if match:
            try:
                value = float(match.group(1).replace(",", "."))
            except ValueError:
                job.log(text)
                continue
            # Логируем только заметные скачки, без дублей.
            if percent_logged is None or abs(value - percent_logged) >= 10.0:
                percent_logged = value
                job.log("апскейл: {:.0f}%".format(value))
            continue
        job.log(text)

    if code != 0:
        raise RuntimeError("Движок апскейла завершился с кодом {}".format(code))


def _count_outputs(out_dir: Path) -> int:
    try:
        return sum(1 for _ in out_dir.iterdir())
    except OSError:
        return 0


def _estimate_chunk_bytes(src_w: int, src_h: int, out_wh: tuple[int, int], fps: float, seconds: float) -> int:
    """PNG во входном кадре + PNG в выходном. Грубая, но достаточная оценка."""
    n = max(1, int(fps * seconds))
    in_png = src_w * src_h * 1.3
    out_png = out_wh[0] * out_wh[1] * 1.3
    return int((in_png + out_png) * n)


def preview_frame(src: Path, dst: Path, at: float) -> Optional[Path]:
    return ffmpeg.first_frame(src, dst, at=at, width=960)
