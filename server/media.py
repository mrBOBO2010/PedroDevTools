"""Простые медиа-операции без нейросетей: ресайз, компресс, конверт, GIF и т.д.

Каждая операция — это запись в OPS: схема полей для формы + функция сборки
ffmpeg-команды. Прогресс для видео идёт по времени через файл -progress,
для мгновенных операций (фото, аудио) — просто 0→100.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import config
import ffmpeg
import jobs

# --- общие кусочки ffmpeg ------------------------------------------------

VIDEO_CONTAINERS = ["mp4", "mkv", "webm"]
IMAGE_FORMATS = ["jpg", "png", "webp"]
AUDIO_FORMATS = ["mp3", "m4a", "ogg", "opus", "wav", "flac"]


def venc_args(o: dict) -> tuple[list[str], str]:
    """Аргументы видеокодека + подходящее расширение."""
    codec = o.get("codec", "libx264")
    crf = str(int(o.get("crf", 20)))
    preset = o.get("x264_preset", "veryfast")
    if codec == "libx265":
        return ["-c:v", "libx265", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p"], "mp4"
    if codec == "libvpx-vp9":
        return ["-c:v", "libvpx-vp9", "-crf", crf, "-b:v", "0", "-row-mt", "1", "-pix_fmt", "yuv420p"], "webm"
    if codec == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", crf, "-b:v", "0", "-pix_fmt", "yuv420p"], "mp4"
    if codec == "hevc_nvenc":
        return ["-c:v", "hevc_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", crf, "-b:v", "0", "-pix_fmt", "yuv420p"], "mp4"
    return ["-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p"], "mp4"


def audio_args(o: dict, key: str = "audio", br_key: str = "audio_bitrate") -> list[str]:
    mode = o.get(key, "copy")
    if mode == "none":
        return ["-an"]
    if mode == "copy":
        return ["-c:a", "copy"]
    return ["-c:a", "aac", "-b:a", "{}k".format(int(o.get(br_key, 128)))]


def fit_filter(w: int, h: int) -> Optional[str]:
    """Ресайз с сохранением пропорций: 0 = авто по второй стороне."""
    if w > 0 and h > 0:
        return "scale={}:{}:force_original_aspect_ratio=decrease".format(w, h)
    if w > 0:
        return "scale={}:-2".format(w)
    if h > 0:
        return "scale=-2:{}".format(h)
    return None


def resize_filter(o: dict, meta: dict) -> Optional[str]:
    """Один фильтр на оба смысла «размера».

    Галочка «сохранять пропорции» стоит — размер задаётся любой стороной,
    вторая считается сама (0 = взять по другой стороне). Галочку сняли —
    ровно width×height: кадр растянется или сплющится, зато попадёт точно
    в нужное разрешение. Ноль в любой из сторон при снятой галочке означает
    «взять размер исходника», а не «вычислить самой».
    """
    w = int(o.get("width") or 0)
    h = int(o.get("height") or 0)
    if o.get("keep_ratio", True):
        return fit_filter(w, h)
    if w <= 0 and h <= 0:
        return None
    if w <= 0:
        w = int(meta.get("width") or 0) or -2
    if h <= 0:
        h = int(meta.get("height") or 0) or -2
    return "scale={}:{}".format(w, h)


def photo_quality_args(fmt: str, quality: int) -> list[str]:
    quality = max(1, min(100, int(quality)))
    if fmt in ("jpg", "jpeg"):
        return ["-q:v", str(max(2, min(31, round(31 - quality * 29 / 100))))]
    if fmt == "webp":
        return ["-quality", str(quality)]
    if fmt == "png":
        return ["-compression_level", str(max(0, min(9, round(9 - quality * 9 / 100))))]
    return []


def audio_codec_args(fmt: str, bitrate: int) -> tuple[list[str], str]:
    bitrate = int(bitrate or 192)
    table = {
        "mp3": (["-c:a", "libmp3lame", "-b:a", "{}k".format(bitrate)], "mp3"),
        "m4a": (["-c:a", "aac", "-b:a", "{}k".format(bitrate)], "m4a"),
        "ogg": (["-c:a", "libvorbis", "-b:a", "{}k".format(bitrate)], "ogg"),
        "opus": (["-c:a", "libopus", "-b:a", "{}k".format(bitrate)], "opus"),
        "wav": (["-c:a", "pcm_s16le"], "wav"),
        "flac": (["-c:a", "flac"], "flac"),
    }
    return table.get(fmt, table["mp3"])


def unique_out(stem: str, suffix: str, ext: str) -> Path:
    base = config.OUT_DIR / "{}_{}.{}".format(stem, suffix, ext)
    if not base.exists():
        return base
    i = 2
    while True:
        cand = config.OUT_DIR / "{}_{}_{}.{}".format(stem, suffix, i, ext)
        if not cand.exists():
            return cand
        i += 1


# --- прогон ---------------------------------------------------------------

def run_simple(job: "jobs.Job", cmd: list[str]) -> None:
    """Мгновенная операция: один вызов ffmpeg без прогресса."""
    job.set_stage("encoding", "Обработка…")
    job.percent = 5.0
    proc = ffmpeg._popen(cmd)
    try:
        while proc.poll() is None:
            job.check_cancel()
            time.sleep(0.3)
        code = proc.wait()
    finally:
        if job.cancelled:
            ffmpeg.terminate(proc)
    if code != 0:
        raise RuntimeError("ffmpeg завершился с кодом {}".format(code))
    job.percent = 100.0


def run_timed(job: "jobs.Job", cmd: list[str], total: float, label: str) -> None:
    """Видео-операция: прогресс по времени через файл -progress."""
    if total <= 0:
        return run_simple(job, cmd)
    prog = config.TMP_DIR / "prog_{}.txt".format(job.id)
    try:
        prog.unlink(missing_ok=True)
    except OSError:
        pass
    job.set_stage("encoding", label)
    started = time.time()
    proc = ffmpeg._popen(cmd, prog)
    try:
        while proc.poll() is None:
            job.check_cancel()
            info = ffmpeg.read_progress(prog)
            try:
                sec = int(info.get("out_time_ms", 0) or 0) / 1000000.0
            except (TypeError, ValueError):
                sec = 0.0
            if sec > 0:
                job.percent = min(99.0, sec * 100.0 / total)
                elapsed = max(0.1, time.time() - started)
                rate = sec / elapsed
                if rate > 0 and sec < total:
                    job.eta = (total - sec) / rate
            time.sleep(0.4)
        code = proc.wait()
    finally:
        if job.cancelled:
            ffmpeg.terminate(proc)
        try:
            prog.unlink(missing_ok=True)
        except OSError:
            pass
    if code != 0:
        raise RuntimeError("ffmpeg завершился с кодом {}".format(code))
    job.percent = 100.0
    job.eta = 0.0


# --- операции --------------------------------------------------------------

def _stem(src: Path, meta: Optional[dict] = None) -> str:
    raw = ""
    if meta:
        raw = meta.get("name") or ""
    name = Path(raw or src.name).stem or "file"
    if len(name) > 40:
        name = name[:40]
    return name


def op_video_resize(job, src, o, meta) -> Path:
    vargs, ext = venc_args(o)
    ext = o.get("container", "mp4")
    if o.get("codec") == "libvpx-vp9":
        ext = "webm"
    cmd = [ffmpeg.find_binary("ffmpeg"), "-y", "-v", "error", "-i", str(src)]
    filt = resize_filter(o, meta)
    if filt:
        cmd += ["-vf", filt]
    cmd += vargs + audio_args(o) + ["-movflags", "+faststart", str(unique_out(_stem(src, meta), "resize", ext))]
    dst = Path(cmd[-1])
    run_timed(job, cmd, float(meta.get("duration") or 0), "Изменение размера…")
    return dst


def op_video_compress(job, src, o, meta) -> Path:
    maxside = int(o.get("maxside") or 0)
    vargs, _ = venc_args(o)
    ext = o.get("container", "mp4")
    if o.get("codec") == "libvpx-vp9":
        ext = "webm"
    cmd = [ffmpeg.find_binary("ffmpeg"), "-y", "-v", "error", "-i", str(src)]
    if maxside > 0:
        cmd += ["-vf", "scale=-2:'min({0},ih)':eval=frame".format(maxside)]
    cmd += vargs + audio_args(o) + ["-movflags", "+faststart"]
    dst = unique_out(_stem(src, meta), "compressed", ext)
    cmd += [str(dst)]
    run_timed(job, cmd, float(meta.get("duration") or 0), "Сжатие…")
    return dst


def op_extract_audio(job, src, o, meta) -> Path:
    fmt = o.get("format", "mp3")
    aargs, ext = audio_codec_args(fmt, int(o.get("bitrate", 192)))
    cmd = [ffmpeg.find_binary("ffmpeg"), "-y", "-v", "error", "-i", str(src), "-vn"] + aargs
    dst = unique_out(_stem(src, meta), "audio", ext)
    cmd += [str(dst)]
    run_timed(job, cmd, float(meta.get("duration") or 0), "Извлечение аудио…")
    return dst


def op_video_gif(job, src, o, meta) -> Path:
    width = max(16, int(o.get("width") or 480))
    fps = min(30.0, max(1.0, float(o.get("fps") or 12)))
    start = max(0.0, float(o.get("start") or 0))
    dur = float(o.get("duration") or 3)
    if dur <= 0:
        dur = min(6.0, max(1.0, float(meta.get("duration") or 3)))
    filt = "fps={:.2f},scale={}:-1:flags=lanczos".format(fps, width)
    tmp = config.TMP_DIR / "gif_{}.png".format(job.id)
    dst = unique_out(_stem(src, meta), "gif", "gif")
    exe = ffmpeg.find_binary("ffmpeg")
    base = [exe, "-y", "-v", "error"]
    if start > 0:
        base += ["-ss", "{:.3f}".format(start)]
    base += ["-t", "{:.3f}".format(dur), "-i", str(src)]
    job.set_stage("encoding", "Палитра…")
    job.percent = 5.0
    pal = list(base) + ["-vf", filt + ",palettegen", str(tmp)]
    run_simple(job, pal)
    job.check_cancel()
    job.set_stage("encoding", "GIF…")
    job.percent = 50.0
    use = list(base) + ["-i", str(tmp), "-lavfi", filt + " [x]; [x][1:v] paletteuse", str(dst)]
    run_simple(job, use)
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass
    return dst


def op_video_speed(job, src, o, meta) -> Path:
    factor = float(o.get("factor") or 1)
    if factor <= 0:
        factor = 1.0
    vargs, _ = venc_args(o)
    ext = o.get("container", "mp4")
    # atempo принимает 0.5..2.0 — раскладываем в цепочку
    rest, chain = factor, []
    while rest > 2.0 + 1e-9:
        chain.append("atempo=2.0")
        rest /= 2.0
    while rest < 0.5 - 1e-9:
        chain.append("atempo=0.5")
        rest /= 0.5
    chain.append("atempo={:.4f}".format(rest))
    cmd = [ffmpeg.find_binary("ffmpeg"), "-y", "-v", "error", "-i", str(src),
           "-vf", "setpts={:.6f}*PTS".format(1.0 / factor),
           "-af", ",".join(chain)] + vargs + audio_args(o) + ["-movflags", "+faststart"]
    dst = unique_out(_stem(src, meta), "x{}".format(str(factor).replace(".", "p")), ext)
    cmd += [str(dst)]
    total = float(meta.get("duration") or 0) / factor
    run_timed(job, cmd, total, "Смена скорости…")
    return dst


def op_photo(job, src, o, meta, mode: str) -> Path:
    fmt = o.get("format", "keep")
    if fmt == "keep":
        fmt = src.suffix.lower().lstrip(".") or "jpg"
    if fmt == "jpeg":
        fmt = "jpg"
    filt = None
    if mode == "resize":
        filt = resize_filter(o, meta)
    elif mode == "compress":
        maxside = int(o.get("maxside") or 0)
        if maxside > 0:
            filt = "scale='min({0},iw)':-2".format(maxside)
    cmd = [ffmpeg.find_binary("ffmpeg"), "-y", "-v", "error", "-i", str(src)]
    if filt:
        cmd += ["-vf", filt]
    cmd += ["-frames:v", "1"] + photo_quality_args(fmt, int(o.get("quality", 85)))
    suffix = {"resize": "resized", "resolution": "res", "compress": "min"}[mode]
    dst = unique_out(_stem(src, meta), suffix, fmt)
    cmd += [str(dst)]
    run_simple(job, cmd)
    return dst


def op_audio_compress(job, src, o, meta) -> Path:
    fmt = o.get("format", "mp3")
    aargs, ext = audio_codec_args(fmt, int(o.get("bitrate", 192)))
    cmd = [ffmpeg.find_binary("ffmpeg"), "-y", "-v", "error", "-i", str(src), "-vn"] + aargs
    rate = int(o.get("rate") or 0)
    if rate > 0:
        cmd += ["-ar", str(rate)]
    ch = int(o.get("channels") or 0)
    if ch in (1, 2):
        cmd += ["-ac", str(ch)]
    dst = unique_out(_stem(src, meta), "conv", ext)
    cmd += [str(dst)]
    run_simple(job, cmd)
    return dst


def op_convert(job, src, o, meta) -> Path:
    fmt = o.get("format", "mp4")
    kind = meta.get("kind", "video")
    exe = ffmpeg.find_binary("ffmpeg")
    stem = _stem(src, meta)
    if kind == "video" and fmt in VIDEO_CONTAINERS:
        if o.get("mode", "copy") == "copy":
            dst = unique_out(stem, "conv", fmt)
            cmd = [exe, "-y", "-v", "error", "-i", str(src), "-c", "copy", str(dst)]
        else:
            vargs, _ = venc_args(o)
            dst = unique_out(stem, "conv", fmt)
            cmd = [exe, "-y", "-v", "error", "-i", str(src)] + vargs + audio_args(o) + ["-movflags", "+faststart", str(dst)]
        run_timed(job, cmd, float(meta.get("duration") or 0), "Конвертация…")
        return dst
    if kind == "video" and fmt in AUDIO_FORMATS:
        return op_extract_audio(job, src, {"format": fmt, "bitrate": o.get("bitrate", 192)}, meta)
    if kind == "video" and fmt in ("jpg", "png"):
        at = max(0.0, float(o.get("at", 1)))
        dst = unique_out(stem, "frame", fmt)
        cmd = [exe, "-y", "-v", "error", "-ss", "{:.3f}".format(at), "-i", str(src),
               "-frames:v", "1"] + photo_quality_args(fmt, 92) + [str(dst)]
        run_simple(job, cmd)
        return dst
    if kind == "image" and fmt in IMAGE_FORMATS:
        dst = unique_out(stem, "conv", fmt)
        cmd = [exe, "-y", "-v", "error", "-i", str(src), "-frames:v", "1"] + photo_quality_args(fmt, int(o.get("quality", 90))) + [str(dst)]
        run_simple(job, cmd)
        return dst
    if kind == "audio" and fmt in AUDIO_FORMATS:
        return op_audio_compress(job, src, {"format": fmt, "bitrate": o.get("bitrate", 192),
                                            "rate": o.get("rate", 0), "channels": o.get("channels", 0)}, meta)
    raise RuntimeError("Такая конвертация не поддерживается: {} → {}".format(kind, fmt))


def op_video_cut(job, src, o, meta) -> Path:
    start = max(0.0, float(o.get("start") or 0))
    dur = float(o.get("duration") or 0)
    total = float(meta.get("duration") or 0)
    if dur <= 0:
        dur = max(0.1, total - start)
    exe = ffmpeg.find_binary("ffmpeg")
    if o.get("mode", "copy") == "copy":
        dst = unique_out(_stem(src, meta), "cut", src.suffix.lower().lstrip(".") or "mp4")
        cmd = [exe, "-y", "-v", "error", "-ss", "{:.3f}".format(start),
               "-t", "{:.3f}".format(dur), "-i", str(src), "-c", "copy", str(dst)]
    else:
        vargs, _ = venc_args(o)
        ext = o.get("container", "mp4")
        if o.get("codec") == "libvpx-vp9":
            ext = "webm"
        dst = unique_out(_stem(src, meta), "cut", ext)
        cmd = [exe, "-y", "-v", "error", "-ss", "{:.3f}".format(start),
               "-t", "{:.3f}".format(dur), "-i", str(src)] + vargs + audio_args(o) + ["-movflags", "+faststart", str(dst)]
    run_timed(job, cmd, dur, "Нарезка…")
    return dst


def op_audio_cut(job, src, o, meta) -> Path:
    """Нарезка музыки: тот же инструмент, что и у видео, но без картинки."""
    start = max(0.0, float(o.get("start") or 0))
    total = float(meta.get("duration") or 0)
    dur = float(o.get("duration") or 0)
    if dur <= 0:
        dur = max(0.1, total - start)
    if start + dur > total > 0:
        dur = max(0.1, total - start)
    fmt = o.get("format", "mp3")
    aargs, ext = audio_codec_args(fmt, int(o.get("bitrate", 192)))
    cmd = [ffmpeg.find_binary("ffmpeg"), "-y", "-v", "error",
           "-ss", "{:.3f}".format(start), "-t", "{:.3f}".format(dur),
           "-i", str(src), "-vn"] + aargs
    dst = unique_out(_stem(src, meta), "cut", ext)
    cmd += [str(dst)]
    run_timed(job, cmd, dur, "Нарезка…")
    return dst


def op_video_rotate(job, src, o, meta) -> Path:
    mode = o.get("mode", "90")
    vf = {"90": "transpose=1", "180": "transpose=2,transpose=2", "270": "transpose=0",
          "fh": "hflip", "fv": "vflip"}.get(mode, "transpose=1")
    vargs, _ = venc_args(o)
    ext = o.get("container", "mp4")
    if o.get("codec") == "libvpx-vp9":
        ext = "webm"
    dst = unique_out(_stem(src, meta), "rot" + str(mode), ext)
    cmd = [ffmpeg.find_binary("ffmpeg"), "-y", "-v", "error", "-i", str(src),
           "-vf", vf] + vargs + audio_args(o) + ["-movflags", "+faststart", str(dst)]
    run_timed(job, cmd, float(meta.get("duration") or 0), "Поворот…")
    return dst


def op_audio_volume(job, src, o, meta) -> Path:
    fmt = o.get("format", "mp3")
    aargs, ext = audio_codec_args(fmt, int(o.get("bitrate", 192)))
    if o.get("loudnorm"):
        af = ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
    else:
        gain = max(-30.0, min(30.0, float(o.get("gain", 6))))
        af = ["-af", "volume={:.1f}dB".format(gain)]
    cmd = [ffmpeg.find_binary("ffmpeg"), "-y", "-v", "error", "-i", str(src), "-vn"] + af + aargs
    rate = int(o.get("rate") or 0)
    if rate > 0:
        cmd += ["-ar", str(rate)]
    dst = unique_out(_stem(src, meta), "vol", ext)
    cmd += [str(dst)]
    run_timed(job, cmd, float(meta.get("duration") or 0), "Громкость…")
    return dst


def op_video_mux(job, src, o, meta, extra=None) -> Path:
    extra = extra or {}
    asrc = extra.get("audio_src")
    if not asrc or not Path(asrc).exists():
        raise RuntimeError("Не загружен аудиофайл для склейки")
    amode = o.get("audio", "aac")
    if amode == "copy":
        aargs = ["-c:a", "copy"]
    elif amode == "none":
        aargs = ["-an"]
    else:
        aargs = ["-c:a", "aac", "-b:a", "{}k".format(int(o.get("audio_bitrate", 192)))]
    ext = o.get("container", "mp4")
    dst = unique_out(_stem(src, meta), "mux", ext)
    cmd = [ffmpeg.find_binary("ffmpeg"), "-y", "-v", "error", "-i", str(src), "-i", str(asrc),
           "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy"] + aargs + ["-shortest", "-movflags", "+faststart", str(dst)]
    run_timed(job, cmd, float(meta.get("duration") or 0), "Склейка…")
    return dst


# --- реестр -----------------------------------------------------------------

def _num(key, label, default, mn, mx, step=1, help=""):
    return {"key": key, "label": label, "type": "number", "def": default,
            "min": mn, "max": mx, "step": step, "help": help}


def _sel(key, label, options, default=None, help=""):
    return {"key": key, "label": label, "type": "select",
            "options": [{"id": o[0], "title": o[1]} for o in options],
            "def": default if default is not None else options[0][0], "help": help}


def _check(key, label, default=False, help=""):
    return {"key": key, "label": label, "type": "check", "def": bool(default), "help": help}


CODEC_OPTS = [("libx264", "H.264"), ("libx265", "H.265"), ("libvpx-vp9", "VP9 (WebM)")]
CONTAINER_OPTS = [("mp4", "MP4"), ("mkv", "MKV"), ("webm", "WebM")]
AUDIO_OPTS = [("copy", "Копировать"), ("aac", "AAC"), ("none", "Убрать")]
OUT_FIELDS = [
    _sel("codec", "Кодек", CODEC_OPTS),
    _num("crf", "CRF (меньше — лучше)", 20, 0, 51),
    _sel("x264_preset", "Пресет", [("ultrafast", "ultrafast"), ("veryfast", "veryfast"), ("faster", "faster"), ("medium", "medium"), ("slow", "slow")], "veryfast"),
    _sel("container", "Контейнер", CONTAINER_OPTS),
    _sel("audio", "Аудио", AUDIO_OPTS),
    _num("audio_bitrate", "Битрейт аудио", 128, 32, 512, 32),
]


def _photo_fields(extra):
    return extra + [
        _sel("format", "Формат", [("keep", "Как в исходнике"), ("jpg", "JPG"), ("png", "PNG"), ("webp", "WebP")]),
        _num("quality", "Качество", 85, 1, 100),
    ]


OPS: dict[str, dict] = {
    "v-size": {
        "title": "Размер и разрешение", "tab": "Видео · размер",
        "desc": "Изменить размер кадра. С пропорциями — задаёшь любую сторону, "
                "вторая считается сама. Без пропорций — ровно ширина × высота.",
        "accept": "video/*", "kinds": ["video"], "submit": "Изменить размер",
        "fields": [
            _num("width", "Ширина, px (0 — авто)", 1280, 0, 7680, 2),
            _num("height", "Высота, px (0 — авто)", 0, 0, 4320, 2),
            _check("keep_ratio", "Сохранять пропорции", True,
                   "Сними галочку, чтобы задать точное разрешение — кадр растянется"),
        ] + OUT_FIELDS,
        "run": op_video_resize,
    },
    "v-comp": {
        "title": "Компрессор видео", "tab": "Видео · компрессор",
        "desc": "Меньший файл тем же кодеком или другим. Качество — через CRF.",
        "accept": "video/*", "kinds": ["video"], "submit": "Сжать",
        "fields": [
            _sel("maxside", "Уменьшить до (0 — без ресайза)", [("0", "Не уменьшать"), ("1920", "1080p"), ("1280", "720p"), ("854", "480p")]),
        ] + OUT_FIELDS,
        "run": op_video_compress,
    },
    "v-audio": {
        "title": "Видео → аудио", "tab": "Видео → аудио",
        "desc": "Вытащить звуковую дорожку в отдельный файл.",
        "accept": "video/*", "kinds": ["video"], "submit": "Извлечь",
        "fields": [
            _sel("format", "Формат", [("mp3", "MP3"), ("m4a", "M4A"), ("wav", "WAV"), ("flac", "FLAC"), ("ogg", "OGG")]),
            _num("bitrate", "Битрейт, кбит/с", 192, 32, 512, 32),
        ],
        "run": op_extract_audio,
    },
    "v-gif": {
        "title": "Видео → GIF", "tab": "Видео → GIF",
        "desc": "Короткий фрагмент в GIF с хорошей палитрой (два прохода).",
        "accept": "video/*", "kinds": ["video"], "submit": "Сделать GIF",
        "fields": [
            _num("width", "Ширина, px", 480, 16, 1920, 2),
            _num("fps", "Кадров в секунду", 12, 1, 30, 1),
            _num("start", "Начало, сек", 0, 0, 86400, 0.5),
            _num("duration", "Длительность, сек", 3, 0.5, 30, 0.5),
        ],
        "run": op_video_gif,
    },
    "v-speed": {
        "title": "Скорость видео", "tab": "Видео · скорость",
        "desc": "Ускорить или замедлить. Звук подстраивается без смены тона.",
        "accept": "video/*", "kinds": ["video"], "submit": "Применить",
        "fields": [
            _sel("factor", "Скорость", [("0.25", "×0.25"), ("0.5", "×0.5"), ("0.75", "×0.75"), ("1.25", "×1.25"), ("1.5", "×1.5"), ("2", "×2"), ("4", "×4")], "2"),
        ] + OUT_FIELDS,
        "run": op_video_speed,
    },
    "v-cut": {
        "title": "Нарезка видео", "tab": "Видео · нарезка",
        "desc": "Вырезать фрагмент по времени. Тяните края на полосе монтажа "
                "или впишите секунды. Быстрый режим — без перекодирования.",
        "accept": "video/*", "kinds": ["video"], "submit": "Вырезать", "scrub": True,
        "fields": [
            _num("start", "Начало, сек", 0, 0, 86400, 0.1),
            _num("duration", "Длительность, сек", 10, 0.1, 86400, 0.1),
            _sel("mode", "Режим", [("copy", "Быстро, без перекодирования"), ("recode", "Точно, с перекодированием")]),
        ] + OUT_FIELDS,
        "run": op_video_cut,
    },
    "a-cut": {
        "title": "Нарезка музыки", "tab": "Музыка · нарезка",
        "desc": "Вырезать кусок трека: края тянутся мышкой по полосе.",
        "accept": "audio/*", "kinds": ["audio"], "submit": "Вырезать", "scrub": True,
        "fields": [
            _num("start", "Начало, сек", 0, 0, 86400, 0.1),
            _num("duration", "Длительность, сек", 10, 0.1, 86400, 0.1),
            _sel("format", "Формат", [("mp3", "MP3"), ("m4a", "M4A"), ("wav", "WAV"), ("flac", "FLAC"), ("ogg", "OGG")]),
            _num("bitrate", "Битрейт, кбит/с", 192, 32, 512, 32),
        ],
        "run": op_audio_cut,
    },
    "v-rotate": {
        "title": "Поворот видео", "tab": "Видео · поворот",
        "desc": "Повернуть кадр или отразить зеркально.",
        "accept": "video/*", "kinds": ["video"], "submit": "Повернуть",
        "fields": [
            _sel("mode", "Как", [("90", "На 90° по часовой"), ("180", "На 180°"), ("270", "На 90° против часовой"), ("fh", "Зеркало по горизонтали"), ("fv", "Зеркало по вертикали")]),
        ] + OUT_FIELDS,
        "run": op_video_rotate,
    },
    "v-mux": {
        "title": "Видео + аудио", "tab": "Видео + аудио",
        "desc": "Приклеить свою звуковую дорожку к видео. Нужно два файла.",
        "accept": "video/*", "kinds": ["video"], "submit": "Склеить", "files": 2,
        "accept2": "audio/*",
        "fields": [
            _sel("container", "Контейнер", CONTAINER_OPTS),
            _sel("audio", "Аудио", [("aac", "AAC"), ("copy", "Копировать"), ("none", "Убрать")], "aac"),
            _num("audio_bitrate", "Битрейт аудио", 192, 32, 512, 32),
        ],
        "run": op_video_mux,
    },
    "p-size": {
        "title": "Размер и разрешение", "tab": "Фото · размер",
        "desc": "Изменить размер картинки. С пропорциями — задаёшь любую сторону. "
                "Без пропорций — ровно ширина × высота.",
        "accept": "image/*", "kinds": ["image"], "submit": "Изменить размер",
        "fields": _photo_fields([
            _num("width", "Ширина, px (0 — авто)", 1920, 0, 16384, 2),
            _num("height", "Высота, px (0 — авто)", 0, 0, 16384, 2),
            _check("keep_ratio", "Сохранять пропорции", True,
                   "Сними галочку, чтобы задать точное разрешение — картинка растянется"),
        ]),
        "run": lambda job, src, o, meta: op_photo(job, src, o, meta, "resize"),
    },
    "p-comp": {
        "title": "Компрессор фото", "tab": "Фото · компрессор",
        "desc": "Меньший вес: качество + формат + предел стороны.",
        "accept": "image/*", "kinds": ["image"], "submit": "Сжать",
        "fields": _photo_fields([
            _sel("maxside", "Длинная сторона, max (0 — без ресайза)", [("0", "Не уменьшать"), ("3840", "3840"), ("1920", "1920"), ("1280", "1280")]),
        ]),
        "run": lambda job, src, o, meta: op_photo(job, src, o, meta, "compress"),
    },
    "a-comp": {
        "title": "Компрессор музыки", "tab": "Музыка · компрессор",
        "desc": "Пережать аудио: формат, битрейт, частота, каналы.",
        "accept": "audio/*", "kinds": ["audio"], "submit": "Сжать",
        "fields": [
            _sel("format", "Формат", [("mp3", "MP3"), ("m4a", "M4A"), ("ogg", "OGG"), ("opus", "Opus"), ("wav", "WAV"), ("flac", "FLAC")]),
            _num("bitrate", "Битрейт, кбит/с", 192, 32, 512, 32),
            _sel("rate", "Частота", [("0", "Как в исходнике"), ("44100", "44100 Гц"), ("48000", "48000 Гц")]),
            _sel("channels", "Каналы", [("0", "Как в исходнике"), ("1", "Моно"), ("2", "Стерео")]),
        ],
        "run": op_audio_compress,
    },
    "a-volume": {
        "title": "Громкость музыки", "tab": "Музыка · громкость",
        "desc": "Сделать громче/тише в децибелах или выровнять под стандарт (EBU R128).",
        "accept": "audio/*", "kinds": ["audio"], "submit": "Применить",
        "fields": [
            _num("gain", "Усиление, дБ (-20..+20)", 6, -20, 20, 0.5),
            _check("loudnorm", "Нормализация EBU R128 (вместо усиления)", False),
            _sel("format", "Формат", [("mp3", "MP3"), ("m4a", "M4A"), ("ogg", "OGG"), ("opus", "Opus"), ("wav", "WAV"), ("flac", "FLAC")]),
            _num("bitrate", "Битрейт, кбит/с", 192, 32, 512, 32),
            _sel("rate", "Частота", [("0", "Как в исходнике"), ("44100", "44100 Гц"), ("48000", "48000 Гц")]),
        ],
        "run": op_audio_volume,
    },
    "convert": {
        "title": "Конвертер", "tab": "Конвертер",
        "desc": "Видео, аудио и картинки — в любой формат. Видео в фото — это кадр на указанной секунде.",
        "accept": "video/*,audio/*,image/*", "kinds": ["video", "audio", "image"], "submit": "Конвертировать",
        "fields": [
            _sel("format", "Во что", [("mp4", "MP4"), ("mkv", "MKV"), ("webm", "WebM"), ("mp3", "MP3"), ("m4a", "M4A"), ("wav", "WAV"), ("flac", "FLAC"), ("ogg", "OGG"), ("jpg", "JPG"), ("png", "PNG")]),
            _sel("mode", "Видео-режим", [("copy", "Без перекодирования"), ("recode", "Перекодировать")]),
            _num("at", "Кадр видео → фото, секунда", 1, 0, 86400, 0.5),
            _num("bitrate", "Битрейт аудио", 192, 32, 512, 32),
            _num("quality", "Качество фото", 90, 1, 100),
        ] + OUT_FIELDS,
        "run": op_convert,
    },
    "meta": {
        "title": "Метаданные", "tab": "Метаданные",
        "desc": "Всё, что известно о файле: потоки, кодеки, разрешение, битрейт.",
        "accept": "video/*,audio/*,image/*", "kinds": ["video", "audio", "image"], "submit": "",
        "fields": [],
        "run": None,
    },
}


def get_op(op_id: str) -> dict:
    op = OPS.get(op_id)
    if not op:
        raise KeyError("Неизвестная операция: " + op_id)
    return op


def schema(op_id: str) -> dict:
    op = get_op(op_id)
    return {
        "id": op_id,
        "title": op["title"],
        "desc": op["desc"],
        "accept": op["accept"],
        "kinds": op["kinds"],
        "submit": op["submit"],
        "fields": op["fields"],
        "files": int(op.get("files", 1)),
        "accept2": op.get("accept2", ""),
        "scrub": bool(op.get("scrub")),
    }


def run_op(job: "jobs.Job", op_id: str, src: Path, options: dict, meta: dict, extra: Optional[dict] = None) -> Path:
    op = get_op(op_id)
    if meta.get("kind") not in op["kinds"]:
        raise RuntimeError("Операция «{}» не подходит для этого файла".format(op["title"]))
    merged = _defaults(op)
    merged.update({k: v for k, v in (options or {}).items() if v is not None})
    job.log("Операция: {}".format(op["title"]))
    if int(op.get("files", 1)) > 1:
        out = op["run"](job, src, merged, meta, extra or {})
    else:
        out = op["run"](job, src, merged, meta)
    try:
        if out.suffix.lower() in ffmpeg.VIDEO_SUFFIXES:
            job.output_meta = ffmpeg.probe(out)
        elif out.suffix.lower() in ffmpeg.IMAGE_SUFFIXES:
            job.output_meta = ffmpeg.probe_any(out)
    except RuntimeError:
        job.output_meta = {}
    job.log("Готово: {} ({:.1f} МБ)".format(out.name, out.stat().st_size / 1048576))
    return out


def _defaults(op: dict) -> dict:
    values: dict = {}
    for field in op["fields"]:
        values[field["key"]] = field.get("def")
    return values
