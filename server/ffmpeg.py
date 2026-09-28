"""Обёртки над ffmpeg/ffprobe: метаданные, кадры, кодирование, склейка, mux."""
from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Callable, Iterable, Optional

import config

CREATE_NO_WINDOW = 0x08000000 if hasattr(subprocess, "STARTUPINFO") else 0

VIDEO_SUFFIXES = {
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".mpg", ".mpeg",
    ".ts", ".m2ts", ".wmv", ".flv", ".ogv", ".3gp",
}

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif", ".gif"}

AUDIO_SUFFIXES = {
    ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".flac",
    ".wma", ".aiff", ".aif",
}

ALL_MEDIA_SUFFIXES = VIDEO_SUFFIXES | IMAGE_SUFFIXES | AUDIO_SUFFIXES

CODEC_PROFILES = {
    "libx264": {
        "ext": "mp4",
        "pix": "yuv420p",
        "args": lambda o: ["-c:v", "libx264", "-preset", o["x264_preset"], "-crf", str(o["crf"])],
    },
    "libx265": {
        "ext": "mp4",
        "pix": "yuv420p",
        "args": lambda o: ["-c:v", "libx265", "-preset", o["x264_preset"], "-crf", str(o["crf"])],
    },
    "libvpx-vp9": {
        "ext": "webm",
        "pix": "yuv420p",
        "args": lambda o: ["-c:v", "libvpx-vp9", "-crf", str(o["crf"]), "-b:v", "0", "-row-mt", "1"],
    },
    "h264_nvenc": {
        "ext": "mp4",
        "pix": "yuv420p",
        "args": lambda o: ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(o["crf"]), "-b:v", "0"],
    },
    "hevc_nvenc": {
        "ext": "mp4",
        "pix": "yuv420p",
        "args": lambda o: ["-c:v", "hevc_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(o["crf"]), "-b:v", "0"],
    },
}


def find_binary(name: str) -> Optional[str]:
    return shutil.which(name)


def have_ffmpeg() -> bool:
    return bool(find_binary("ffmpeg") and find_binary("ffprobe"))


def run(cmd: list[str], on_line: Optional[Callable[[str], None]] = None) -> subprocess.CompletedProcess:
    """Запуск без окна консоли, с живой выдачей в колбэк."""
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        startupinfo=startupinfo,
        creationflags=CREATE_NO_WINDOW,
    )
    # communicate(), а не wait()+read(): иначе процесс залипает, когда
    # stderr/stdout переполняет буфер — так он и вставал на probe.
    out, _ = proc.communicate()
    out = out or ""
    if on_line and out:
        for line in out.splitlines():
            on_line(line)
    return subprocess.CompletedProcess(cmd, proc.returncode, out)


def _popen(cmd: list[str], progress_path: Optional[Path] = None) -> "subprocess.Popen[str]":
    """Живой процесс ffmpeg без окна. progress_path — файл для -progress."""
    full = list(cmd)
    if progress_path is not None:
        full += ["-progress", str(progress_path), "-nostats"]
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return subprocess.Popen(
        full,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        startupinfo=startupinfo,
        creationflags=CREATE_NO_WINDOW,
    )


def terminate(proc: "subprocess.Popen") -> None:
    try:
        proc.terminate()
        proc.wait(timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
        except OSError:
            pass


def read_progress(path: Path) -> dict:
    """Читает файл -progress: out_time_ms / speed / progress."""
    data: dict[str, str] = {}
    try:
        for line in path.read_text("utf-8", errors="replace").splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                data[key.strip()] = value.strip()
    except OSError:
        pass
    return data


def probe_any(path: Path) -> dict:
    """Метаданные любого медиа: видео, картинка или аудио."""
    exe = find_binary("ffprobe")
    if not exe:
        raise RuntimeError("ffprobe не найден в PATH")
    result = run([
        exe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    if result.returncode != 0:
        raise RuntimeError("ffprobe не смог прочитать файл")
    data = json.loads(result.stdout or "{}")

    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    suffix = path.suffix.lower()

    base = {
        "path": str(path),
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "container": data.get("format", {}).get("format_name", "?"),
        "bitrate": int(data.get("format", {}).get("bit_rate", 0) or 0),
        "has_video": video is not None,
        "has_audio": audio is not None,
        "audio_codec": (audio or {}).get("codec_name", ""),
    }

    duration = 0.0
    for key in ("duration",):
        try:
            duration = float(data.get("format", {}).get(key, 0) or 0)
            if duration > 0:
                break
        except (TypeError, ValueError):
            duration = 0.0

    if video is not None and (suffix in VIDEO_SUFFIXES or (duration > 0.05)):
        base.update(probe(path))
        base["kind"] = "video"
        return base

    if video is not None:
        base["kind"] = "image"
        base["width"] = int(video.get("width", 0))
        base["height"] = int(video.get("height", 0))
        base["video_codec"] = video.get("codec_name", "?")
        base["pix_fmt"] = video.get("pix_fmt", "")
        base["frames"] = 1
        base["duration"] = 0.0
        base["fps"] = 0.0
        return base

    if audio is not None:
        base["kind"] = "audio"
        base["duration"] = duration or float(audio.get("duration", 0) or 0)
        base["audio_channels"] = audio.get("channels", 0)
        base["audio_rate"] = audio.get("sample_rate", 0)
        base["frames"] = 0
        return base

    raise RuntimeError("Не нашёл ни видео, ни аудио, ни картинки")


def _fraction(text: str, default: float = 0.0) -> float:
    try:
        return float(Fraction(text))
    except (ValueError, ZeroDivisionError):
        return default


def probe(path: Path) -> dict:
    """Полные метаданные файла: видеопоток, аудиопоток, число кадров."""
    exe = find_binary("ffprobe")
    if not exe:
        raise RuntimeError("ffprobe не найден в PATH")
    result = run([
        exe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    if result.returncode != 0:
        raise RuntimeError("ffprobe не смог прочитать файл")
    data = json.loads(result.stdout or "{}")

    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not video:
        raise RuntimeError("В файле нет видеопотока")

    fps_text = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
    fps = _fraction(fps_text)
    duration = _fraction(video.get("duration", "0")) or _fraction(
        data.get("format", {}).get("duration", "0")
    )
    frames = 0
    for key in ("nb_frames", "nb_read_frames"):
        try:
            frames = int(video.get(key, 0) or 0)
            if frames:
                break
        except (TypeError, ValueError):
            frames = 0
    if not frames and fps > 0 and duration > 0:
        frames = int(round(fps * duration))

    width = int(video.get("width", 0))
    height = int(video.get("height", 0))

    # С учётом чётности: yuv420p требует чётных размеров.
    pix_fmt = video.get("pix_fmt", "")
    if pix_fmt.startswith(("yuv", "nv12", "nv21")) and "444" not in pix_fmt:
        width -= width % 2
        height -= height % 2

    return {
        "path": str(path),
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "fps_text": fps_text,
        "duration": round(duration, 3),
        "frames": frames,
        "video_codec": video.get("codec_name", "?"),
        "pix_fmt": pix_fmt,
        "profile": video.get("profile", ""),
        "bitrate": int(data.get("format", {}).get("bit_rate", 0) or 0),
        "has_audio": audio is not None,
        "audio_codec": (audio or {}).get("codec_name", ""),
        "audio_channels": (audio or {}).get("channels", 0),
        "audio_rate": (audio or {}).get("sample_rate", 0),
        "container": data.get("format", {}).get("format_name", "?"),
    }


def first_frame(src: Path, dst: Path, at: float = 0.5, width: int = 960) -> Optional[Path]:
    """PNG-превью: кадр с указанной секунды, вписанный по ширине."""
    exe = find_binary("ffmpeg")
    if not exe:
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    result = run([
        exe, "-y", "-v", "error",
        "-ss", "{:.3f}".format(max(0.0, at)),
        "-i", str(src), "-frames:v", "1",
        "-vf", "scale={}:-2:flags=lanczos".format(width),
        "-f", "image2", str(dst),
    ])
    return dst if result.returncode == 0 and dst.exists() else None


def _fps_mode_args() -> list[str]:
    """Режим передискретизации кадров.

    В ffmpeg 5+ -vsync удалён, его заменил -fps_mode. Применяется cfr: мы
    явно задаём целевую частоту через -r, а passthrough с -r вместе считается
    противоречивым и валит вывод. На старых сборках остаётся -vsync cfr.
    """
    exe = find_binary("ffmpeg")
    if not exe:
        return ["-vsync", "cfr"]
    text = run([exe, "-hide_banner", "-h", "full"]).stdout or ""
    if "fps_mode" in text:
        return ["-fps_mode", "cfr"]
    return ["-vsync", "cfr"]


_fps_mode: Optional[list[str]] = None


def fps_mode() -> list[str]:
    global _fps_mode
    if _fps_mode is None:
        _fps_mode = _fps_mode_args()
    return _fps_mode


def extract_frames(src: Path, out_dir: Path, start: float, duration: float, fps: float) -> list[Path]:
    """Распаковывает чанк в PNG. Порядковые имена %06d — пригодны для concat."""
    exe = find_binary("ffmpeg")
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "-y", "-v", "error"]
    if start > 0:
        cmd += ["-ss", "{:.3f}".format(start)]
    cmd += ["-t", "{:.3f}".format(duration), "-i", str(src), "-an"] + fps_mode()
    if fps > 0:
        cmd += ["-r", "{:.6f}".format(fps)]
    cmd += ["-start_number", "0", "-compression_level", "3", str(out_dir / "%06d.png")]
    if run(cmd).returncode != 0:
        raise RuntimeError("ffmpeg не смог распаковать кадры чанка")
    return sorted(out_dir.glob("*.png"))


def encode_chunk(frames_dir: Path, dst: Path, opts: dict, fps: float, scale_wh: Optional[tuple] = None) -> Path:
    """Кодирует кадры чанка в видеофайл без звука. Параметры идентичны во всех чанках.

    scale_wh — финальный размер кадра; отличается от размера кадров, только
    когда модель не может дать точное разрешение и нужен доводящий ресайз.
    """
    exe = find_binary("ffmpeg")
    profile = CODEC_PROFILES.get(opts["codec"], CODEC_PROFILES["libx264"])
    dst.parent.mkdir(parents=True, exist_ok=True)
    args = profile["args"](opts)
    cmd = [
        exe, "-y", "-v", "error",
        "-framerate", "{:.6f}".format(fps),
        "-i", str(frames_dir / "%06d.png"),
    ]
    if scale_wh:
        cmd += ["-vf", "scale={}:{}:flags=lanczos".format(int(scale_wh[0]), int(scale_wh[1]))]
    cmd += args + ["-pix_fmt", profile["pix"], "-r", "{:.6f}".format(fps)]
    if opts.get("crf_mode") == "bitrate":
        cmd += ["-b:v", "{}k".format(int(opts.get("bitrate", 8000)))]
    cmd += ["-an", str(dst)]
    if run(cmd).returncode != 0:
        raise RuntimeError("ffmpeg не смог закодировать чанк")
    return dst


def concat_chunks(segments: Iterable[Path], dst: Path) -> Path:
    """Склейка видеодорожек через concat-демуксер с копированием потока."""
    exe = find_binary("ffmpeg")
    segments = list(segments)
    if not segments:
        raise RuntimeError("Нет чанков для склейки")
    if len(segments) == 1:
        shutil.copyfile(segments[0], dst)
        return dst

    listing = dst.parent / "concat.txt"
    listing.write_text(
        "".join("file '{}'\n".format(p.as_posix()) for p in segments), "utf-8"
    )
    result = run([
        exe, "-y", "-v", "error", "-f", "concat", "-safe", "0",
        "-i", str(listing), "-c", "copy", str(dst),
    ])
    listing.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError("Не удалось склеить чанки")
    return dst


def mux_audio(video: Path, src: Path, dst: Path, opts: dict, start: float, duration: float) -> Path:
    """Финальная сборка: видео из чанков + исходное аудио с учётом диапазона.

    Видео уже нарезано по диапазону на этапе чанков — его больше не трогаем.
    -ss/-t даём только аудио-входу (как входные опции, до -i): раньше они стояли
    как выходные и при start>0 отрезали от готового видео лишние секунды.
    """
    exe = find_binary("ffmpeg")
    profile = CODEC_PROFILES.get(opts["codec"], CODEC_PROFILES["libx264"])
    cmd = [exe, "-y", "-v", "error", "-i", str(video)]
    audio = opts.get("audio", "copy")

    if audio != "none":
        if start > 0:
            cmd += ["-ss", "{:.3f}".format(start)]
        if duration > 0:
            cmd += ["-t", "{:.3f}".format(duration)]
        cmd += ["-i", str(src)]

    cmd += ["-map", "0:v:0"]
    if audio == "copy":
        cmd += ["-map", "1:a:0?", "-c:a", "copy"]
    elif audio != "none":
        cmd += [
            "-map", "1:a:0?", "-c:a", "aac",
            "-b:a", "{}k".format(int(opts.get("audio_bitrate", 192))),
        ]

    if audio == "none":
        cmd += ["-an"]
    else:
        cmd += ["-shortest"]

    cmd += ["-movflags", "+faststart", str(dst)]
    if run(cmd).returncode != 0:
        raise RuntimeError("Не удалось собрать итоговый файл")
    return dst


def extract_segment(src: Path, dst: Path, start: float, duration: float) -> Path:
    """Превью-фрагмент: кадры для сравнения «до/после» на одном таймкоде."""
    exe = find_binary("ffmpeg")
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "-y", "-v", "error"]
    if start > 0:
        cmd += ["-ss", "{:.3f}".format(start)]
    cmd += ["-i", str(src), "-frames:v", "1", str(dst)]
    run(cmd)
    return dst


def free_space(path: Path) -> int:
    return config.free_bytes(path)
