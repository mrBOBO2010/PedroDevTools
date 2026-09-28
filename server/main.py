"""FastAPI: REST + WebSocket для интерфейса PedroDevTools."""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

# Запуск и как `python server/main.py`, и как `uvicorn server.main:app` из корня.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import config
import devices
import ffmpeg
import imgup
import media
import models
import upscale
from jobs import MANAGER, Cancelled

app = FastAPI(title="PedroDevTools", docs_url=None, redoc_url=None)
config.ensure_dirs()
models.cleanup_temp()

UPLOAD_DIR = config.TMP_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
NAMES_DIR = UPLOAD_DIR / "names"
NAMES_DIR.mkdir(parents=True, exist_ok=True)


def _remember_name(token: str, original: str) -> None:
    try:
        (NAMES_DIR / "{}.txt".format(token)).write_text(original, "utf-8")
    except OSError:
        pass


def _original_name(token: str, fallback: str = "") -> str:
    try:
        name = (NAMES_DIR / "{}.txt".format(Path(token.strip()).stem[:10])).read_text("utf-8").strip()
        return name or fallback
    except OSError:
        return fallback

TAB_GROUPS = [
    {"id": "video", "title": "Видео"},
    {"id": "photo", "title": "Фото"},
    {"id": "audio", "title": "Аудио"},
    {"id": "files", "title": "Файлы"},
]

TABS = [
    {"id": "upscale", "title": "Апскейл видео", "hint": "нейросеть", "ready": True, "group": "video"},
    {"id": "v-size", "title": "Размер", "hint": "", "ready": True, "group": "video"},
    {"id": "v-comp", "title": "Компрессор", "hint": "", "ready": True, "group": "video"},
    {"id": "v-speed", "title": "Скорость", "hint": "", "ready": True, "group": "video"},
    {"id": "v-cut", "title": "Нарезка", "hint": "мышкой", "ready": True, "group": "video"},
    {"id": "v-rotate", "title": "Поворот", "hint": "", "ready": True, "group": "video"},
    {"id": "v-audio", "title": "Видео → аудио", "hint": "", "ready": True, "group": "video"},
    {"id": "v-gif", "title": "Видео → GIF", "hint": "", "ready": True, "group": "video"},
    {"id": "v-mux", "title": "Видео + аудио", "hint": "", "ready": True, "group": "video"},
    {"id": "p-upscale", "title": "Апскейл фото", "hint": "нейросеть", "ready": True, "group": "photo"},
    {"id": "p-size", "title": "Размер", "hint": "", "ready": True, "group": "photo"},
    {"id": "p-comp", "title": "Компрессор", "hint": "", "ready": True, "group": "photo"},
    {"id": "a-comp", "title": "Компрессор", "hint": "", "ready": True, "group": "audio"},
    {"id": "a-volume", "title": "Громкость", "hint": "", "ready": True, "group": "audio"},
    {"id": "a-cut", "title": "Нарезка", "hint": "мышкой", "ready": True, "group": "audio"},
    {"id": "convert", "title": "Конвертер", "hint": "всё во всё", "ready": True, "group": "files"},
    {"id": "meta", "title": "Метаданные", "hint": "", "ready": True, "group": "files"},
]


def json_error(message: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=code)


@app.get("/api/health")
def health() -> dict:
    probe = devices.probe()
    return {
        "ok": ffmpeg.have_ffmpeg(),
        "ffmpeg": ffmpeg.find_binary("ffmpeg") or "",
        "ffprobe": ffmpeg.find_binary("ffprobe") or "",
        "ffmpeg_version": _ffmpeg_version(),
        "ffmpeg_short": _ffmpeg_short(),
        "devices": probe,
        "engines": {k: {"title": v["title"], "installed": v["exe"].exists()} for k, v in config.ENGINES.items()},
        "models_mb": models.models_size_mb(),
        "free_mb": config.free_bytes(config.TMP_DIR) // 1024 // 1024,
        "cores": probe["logical_cores"],
        "version": "1.0",
    }


def _ffmpeg_version() -> str:
    exe = ffmpeg.find_binary("ffmpeg")
    if not exe:
        return ""
    result = ffmpeg.run([exe, "-version"])
    first = (result.stdout or "").splitlines()
    return first[0] if first else ""


def _ffmpeg_short() -> str:
    """Коротко для панели: 9.0 или дата сборки вместо простыни."""
    import re

    full = _ffmpeg_version()
    match = re.search(r"version (\S+)", full)
    if not match:
        return "?"
    ver = match.group(1)
    if re.match(r"^\d+\.\d+", ver):
        return re.match(r"^(\d+\.\d+(?:\.\d+)?)", ver).group(1)
    # Сборки gyan.dev печатают дату: «2025-12-07-git-c4d22f2d2c-…»
    dated = re.match(r"^(\d{4}-\d{2}-\d{2})", ver)
    if dated:
        return dated.group(1)
    if len(ver) > 14:
        return ver[:10] + "…"
    return ver


@app.get("/api/tabs")
def tabs() -> dict:
    return {"tabs": TABS, "groups": TAB_GROUPS}


@app.get("/api/models")
def list_models(engine: Optional[str] = None) -> dict:
    return {"models": models.list_models(engine)}


@app.post("/api/models/{model_id}/install")
def install_model(model_id: str) -> dict:
    try:
        return models.install(model_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        return json_error(str(exc), 500)


@app.delete("/api/models/{model_id}")
def remove_model(model_id: str) -> dict:
    try:
        return models.remove(model_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/models/{model_id}/stream")
async def model_progress(model_id: str) -> StreamingResponse:
    return StreamingResponse(_model_progress_stream(model_id), media_type="text/event-stream")


async def _model_progress_stream(model_id: str):
    last = None
    deadline = time.time() + 900
    while time.time() < deadline:
        data = models.get_progress(model_id)
        if data != last:
            last = data
            yield "data: {}\n\n".format(json.dumps(data, ensure_ascii=False))
            if data and data.get("state") in ("done", "error"):
                return
        await asyncio.sleep(0.5)
    yield "data: {}\n\n".format(json.dumps({"state": "timeout"}, ensure_ascii=False))


def _save_upload(upload: UploadFile) -> tuple[Path, str, str]:
    """Сохраняет файл во временный каталог. Возвращает (путь, токен, имя)."""
    original = Path(upload.filename or "video.mp4").name
    suffix = Path(original).suffix.lower() or ".mp4"
    if suffix not in ffmpeg.VIDEO_SUFFIXES:
        raise HTTPException(400, "Неподдерживаемый формат: " + suffix)
    token = uuid.uuid4().hex[:10]
    dest = UPLOAD_DIR / "{}{}".format(token, suffix)
    with open(dest, "wb") as fh:
        shutil.copyfileobj(upload.file, fh, length=1024 * 1024)
    if dest.stat().st_size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "Пустой файл")
    _remember_name(token, original)
    return dest, token, original


@app.post("/api/probe")
async def probe_video(file: UploadFile = File(...)) -> dict:
    if not ffmpeg.have_ffmpeg():
        return json_error("ffmpeg не найден в PATH", 503)
    path, token, original = _save_upload(file)
    try:
        meta = ffmpeg.probe(path)
    except RuntimeError as exc:
        path.unlink(missing_ok=True)
        return json_error(str(exc), 400)

    meta["name"] = original
    preview = ffmpeg.first_frame(path, UPLOAD_DIR / "{}_preview.png".format(token), at=min(1.0, meta["duration"] * 0.25))
    meta["token"] = token
    meta["preview_url"] = "/api/preview/{}".format(token) if preview else None
    # Сам файл для предпросмотра с плеером, а не только кадр.
    meta["source_url"] = "/api/file/{}?inline=1".format(token)

    settings = config.load_settings()
    try:
        model = upscale.pick_model(settings, settings.get("engine", "auto"))
        target = upscale.compute_target(meta, settings, model.get("scale"))
        meta["suggested"] = target
    except (KeyError, ValueError, RuntimeError):
        pass
    return {"meta": meta}


@app.get("/api/preview/{token}")
def preview(token: str) -> FileResponse:
    path = UPLOAD_DIR / "{}_preview.png".format(token)
    if not path.exists():
        raise HTTPException(404, "Превью не найдено")
    return FileResponse(path, media_type="image/png")


@app.get("/api/file/{token}")
def source_file(token: str, inline: int = 0) -> FileResponse:
    """Исходник по токену. inline=1 — отдать как есть, чтобы браузер играл,
    а не скачивал: этим пользуется предпросмотр видео в панели."""
    src = _find_source(token)
    if inline:
        return FileResponse(src, media_type=_guess_media_type(src))
    return FileResponse(src, filename=src.name, media_type="application/octet-stream")


@app.get("/api/preview-frame/{token}")
def preview_frame(token: str, at: float = 0.0, width: int = 960) -> FileResponse:
    src = _find_source(token)
    out = UPLOAD_DIR / "{}_at{:.2f}_{}.png".format(token, at, width)
    got = ffmpeg.first_frame(src, out, at=at, width=width)
    if not got:
        raise HTTPException(404, "Кадр не найден")
    return FileResponse(got, media_type="image/png")


def _find_source(token: str, suffixes: Optional[set] = None) -> Path:
    """Исходник по токену. Токен — это имя файла без расширения."""
    if not isinstance(token, str) or not token.strip():
        raise HTTPException(400, "Некорректный токен файла")
    # Токен всегда ровно 10 hex-символов; лишнее отрезаем на случай query-мусора.
    token = Path(token.strip()).stem[:10]
    allowed = suffixes or ffmpeg.VIDEO_SUFFIXES
    for path in sorted(UPLOAD_DIR.glob("{}.*".format(token))):
        if "_preview" in path.stem or "_at" in path.stem:
            continue
        if path.suffix.lower() in allowed:
            return path
    raise HTTPException(404, "Файл не найден")


@app.post("/api/jobs")
async def create_job(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return json_error("Ожидался JSON-объект", 400)
    raw_token = payload.get("token")
    raw_options = payload.get("options")
    if isinstance(raw_token, dict):
        # Клиент прислал объект — берём первый строковый скаляр из него.
        raw_token = next(
            (v for v in raw_token.values() if isinstance(v, str) and v.strip()), None
        )
    token = raw_token if isinstance(raw_token, str) else ""
    options = raw_options if isinstance(raw_options, dict) else {}
    if not token.strip():
        return json_error("Не указан файл", 400)
    try:
        src = _find_source(token)
    except HTTPException as exc:
        return json_error(str(exc.detail), 404)

    try:
        meta = ffmpeg.probe(src)
    except RuntimeError as exc:
        return json_error(str(exc), 400)

    merged = config.load_settings()
    merged.update({k: v for k, v in options.items() if v is not None})

    try:
        engine = upscale.resolve_engine(merged)
        model = upscale.pick_model(merged, engine)
        target = upscale.compute_target(meta, merged, model.get("scale"))
    except RuntimeError as exc:
        return json_error(str(exc), 400)

    merged["engine_resolved"] = engine
    merged["model_resolved"] = model["id"]
    merged["target"] = target
    merged["needs_model"] = not model["installed"]

    if merged.get("audio") != "none" and not meta.get("has_audio"):
        merged["audio"] = "none"

    job = MANAGER.create(src, merged, meta)
    job.log("Задача создана: {}".format(job.original_name))
    job.log("Итог: {}×{} → {}×{} (×{})".format(
        meta["width"], meta["height"], target["target_w"], target["target_h"], target["exact_factor"]
    ))

    threading.Thread(target=_run_job, args=(job, token), daemon=True).start()
    return {"job": job.public()}


def _run_job(job, token: str) -> None:
    def worker(current):
        if current.options.get("needs_model"):
            current.set_stage("preparing", "скачивание модели")
            models.install(current.options["model_resolved"], current.log)
        return upscale.upscale(current)

    try:
        MANAGER.run(job, worker)
    finally:
        MANAGER.cleanup(job)
        try:
            job.log("Временные файлы удалены", "muted")
        except Exception:  # noqa: BLE001
            pass


def _save_media_upload(upload: UploadFile) -> tuple[Path, str, str]:
    original = Path(upload.filename or "file").name
    suffix = Path(original).suffix.lower()
    if suffix not in ffmpeg.ALL_MEDIA_SUFFIXES:
        raise HTTPException(400, "Неподдерживаемый формат: " + (suffix or "без расширения"))
    token = uuid.uuid4().hex[:10]
    dest = UPLOAD_DIR / "{}{}".format(token, suffix)
    with open(dest, "wb") as fh:
        shutil.copyfileobj(upload.file, fh, length=1024 * 1024)
    if dest.stat().st_size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "Пустой файл")
    _remember_name(token, original)
    return dest, token, original


@app.post("/api/media/probe")
async def media_probe(file: UploadFile = File(...)) -> dict:
    if not ffmpeg.have_ffmpeg():
        return json_error("ffmpeg не найден в PATH", 503)
    path, token, original = _save_media_upload(file)
    try:
        meta = ffmpeg.probe_any(path)
    except RuntimeError as exc:
        path.unlink(missing_ok=True)
        return json_error(str(exc), 400)
    meta["name"] = original
    meta["token"] = token
    if meta.get("kind") == "video":
        preview = ffmpeg.first_frame(
            path, UPLOAD_DIR / "{}_preview.png".format(token),
            at=min(1.0, (meta.get("duration") or 1) * 0.25),
        )
        meta["preview_url"] = "/api/preview/{}".format(token) if preview else None
    elif meta.get("kind") in ("image", "audio"):
        # Картинку видно картинкой, трек — плеером: файл отдаётся inline.
        meta["preview_url"] = "/api/media/file/{}?inline=1".format(token)
    else:
        meta["preview_url"] = None
    return {"meta": meta}


@app.get("/api/media/file/{token}")
def media_file(token: str, inline: int = 0) -> FileResponse:
    src = _find_source(token, ffmpeg.ALL_MEDIA_SUFFIXES)
    if inline:
        return FileResponse(src, media_type=_guess_media_type(src))
    return FileResponse(src, filename=src.name, media_type="application/octet-stream")


@app.get("/api/media/cut-frame/{token}")
def media_cut_frame(token: str, at: float = 0.0, width: int = 640) -> FileResponse:
    """Кадр для полосы нарезки. Один и тот же файл перезаписывается, чтобы
    перетаскивание не плодило тысячу PNG во временном каталоге."""
    src = _find_source(token, ffmpeg.ALL_MEDIA_SUFFIXES)
    if src.suffix.lower() not in ffmpeg.VIDEO_SUFFIXES:
        raise HTTPException(400, "Кадр бывает только у видео")
    out = UPLOAD_DIR / "{}_cut.png".format(token)
    got = ffmpeg.first_frame(src, out, at=max(0.0, at), width=clamp(width, 80, 1920))
    if not got:
        raise HTTPException(404, "Кадр не найден")
    return FileResponse(got, media_type="image/png")


@app.get("/api/media/ops")
def media_ops() -> dict:
    return {"ops": [media.schema(op_id) for op_id in media.OPS]}


@app.get("/api/media/ops/{op_id}")
def media_op(op_id: str) -> dict:
    try:
        return {"op": media.schema(op_id)}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/media/jobs")
async def media_create_job(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return json_error("Ожидался JSON-объект", 400)
    op_id = payload.get("op")
    token = payload.get("token")
    options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
    if not op_id or not token:
        return json_error("Нужны op и token", 400)
    try:
        op = media.get_op(op_id)
    except KeyError as exc:
        return json_error(str(exc), 404)
    try:
        src = _find_source(token, ffmpeg.ALL_MEDIA_SUFFIXES)
    except HTTPException as exc:
        return json_error(str(exc.detail), 404)
    try:
        meta = ffmpeg.probe_any(src)
    except RuntimeError as exc:
        return json_error(str(exc), 400)
    meta["name"] = _original_name(token, src.name)
    if meta.get("kind") not in op["kinds"]:
        return json_error("Файл не подходит для операции «{}»".format(op["title"]), 400)

    extra: dict = {}
    if int(op.get("files", 1)) > 1:
        token2 = payload.get("token2")
        if not token2:
            return json_error("Загрузите второй файл", 400)
        try:
            asrc = _find_source(token2, ffmpeg.ALL_MEDIA_SUFFIXES)
            ameta = ffmpeg.probe_any(asrc)
        except (HTTPException, RuntimeError) as exc:
            detail = getattr(exc, "detail", str(exc))
            return json_error("Второй файл: {}".format(detail), 400)
        if ameta.get("kind") != "audio":
            return json_error("Вторым файлом должна быть музыка/аудио", 400)
        extra["audio_src"] = str(asrc)
        extra["audio_name"] = _original_name(token2, asrc.name)

    job = MANAGER.create(src, {"op": op_id, "options": options}, meta)
    job.frames_total = 1000
    job.log("Задача создана: {}".format(meta["name"]))

    def worker(current):
        return media.run_op(current, op_id, src, options, meta, extra)

    threading.Thread(target=_run_media_job, args=(job, worker), daemon=True).start()
    return {"job": job.public()}


def _run_media_job(job, worker) -> None:
    try:
        MANAGER.run(job, worker)
    finally:
        MANAGER.cleanup(job)


@app.post("/api/imgup/jobs")
async def imgup_create_job(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return json_error("Ожидался JSON-объект", 400)
    token = payload.get("token")
    options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
    if not token:
        return json_error("Не указан файл", 400)
    try:
        src = _find_source(token, ffmpeg.ALL_MEDIA_SUFFIXES)
    except HTTPException as exc:
        return json_error(str(exc.detail), 404)
    try:
        meta = ffmpeg.probe_any(src)
    except RuntimeError as exc:
        return json_error(str(exc), 400)
    meta["name"] = _original_name(token, src.name)
    if meta.get("kind") != "image":
        return json_error("Сюда нужно фото, а не {}".format(meta.get("kind", "?")), 400)

    job = MANAGER.create(src, {"imgup": True, "options": options}, meta)
    job.frames_total = 100
    job.log("Задача создана: {}".format(meta["name"]))

    def worker(current):
        return imgup.upscale_image(current, src, options, meta)

    threading.Thread(target=_run_media_job, args=(job, worker), daemon=True).start()
    return {"job": job.public()}


@app.get("/api/jobs")
def list_jobs() -> dict:
    return {"jobs": MANAGER.list()}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = MANAGER.get(job_id)
    if not job:
        raise HTTPException(404, "Задача не найдена")
    return {"job": job.public()}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    job = MANAGER.get(job_id)
    if not job:
        raise HTTPException(404, "Задача не найдена")
    job.cancel()
    return {"job": job.public()}


@app.get("/api/jobs/{job_id}/download")
def download_job(job_id: str, inline: int = 0) -> FileResponse:
    job = MANAGER.get(job_id)
    if not job or not job.output or not job.output.exists():
        raise HTTPException(404, "Файл не готов")
    if inline:
        return FileResponse(job.output, media_type=_guess_media_type(job.output))
    return FileResponse(job.output, filename=job.output.name, media_type="application/octet-stream")


def _guess_media_type(path: Path) -> Optional[str]:
    return {
        ".mp4": "video/mp4", ".mkv": "video/x-matroska", ".webm": "video/webm",
        ".mov": "video/quicktime", ".avi": "video/x-msvideo", ".m4v": "video/mp4",
        ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".ogg": "audio/ogg",
        ".opus": "audio/ogg", ".oga": "audio/ogg", ".wav": "audio/wav",
        ".flac": "audio/flac", ".aac": "audio/aac",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
        ".tif": "image/tiff", ".tiff": "image/tiff",
    }.get(path.suffix.lower())


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


@app.get("/api/jobs/{job_id}/frame")
def job_frame(job_id: str, at: float = 0.0, width: int = 960) -> FileResponse:
    """Кадр из готового файла — для сравнения «до/после» на одном таймкоде."""
    job = MANAGER.get(job_id)
    if not job or not job.output or not job.output.exists():
        raise HTTPException(404, "Результат не готов")
    out = UPLOAD_DIR / "{}_after_{:.2f}_{}.png".format(job_id, at, width)
    got = ffmpeg.first_frame(job.output, out, at=at, width=width)
    if not got:
        raise HTTPException(404, "Кадр не найден")
    return FileResponse(got, media_type="image/png")


@app.get("/api/jobs/{job_id}/logs")
def job_logs(job_id: str, since: int = 0) -> dict:
    job = MANAGER.get(job_id)
    if not job:
        raise HTTPException(404, "Задача не найдена")
    return {"logs": job.logs[since:]}


@app.websocket("/api/jobs/{job_id}/events")
async def job_events(websocket: WebSocket, job_id: str) -> None:
    await websocket.accept()
    last_log = 0
    try:
        while True:
            job = MANAGER.get(job_id)
            if not job:
                await websocket.send_json({"error": "Задача не найдена"})
                return
            await websocket.send_json({"job": job.public(), "logs": job.logs[last_log:]})
            last_log = len(job.logs)
            if job.stage in ("done", "failed", "cancelled"):
                return
            await asyncio.sleep(0.5)
    except (WebSocketDisconnect, RuntimeError):
        return


@app.get("/api/settings")
def get_settings() -> dict:
    return {"settings": config.load_settings()}


@app.put("/api/settings")
async def put_settings(payload: dict) -> dict:
    values = payload.get("settings") or payload
    return {"settings": config.save_settings(values)}


@app.get("/api/presets")
def get_presets() -> dict:
    return {
        "built_in": upscale_presets(),
        "user": config.load_user_presets(),
    }


def upscale_presets() -> list[dict]:
    return [
        {
            "id": "quick",
            "title": "Быстрый просмотр",
            "note": "×2, быстрый кодек — для оценки результата",
            "values": {
                "scale_mode": "percent", "percent": 200, "codec": "libx264",
                "crf": 22, "crf_mode": "crf", "x264_preset": "veryfast", "container": "mp4",
            },
        },
        {
            "id": "yt1080",
            "title": "YouTube 1080p",
            "note": "До 1920 по длинной стороне, CRF 17, slow",
            "values": {
                "scale_mode": "long_edge", "long_edge": 1920, "codec": "libx264",
                "crf": 17, "crf_mode": "crf", "x264_preset": "slow", "container": "mp4",
            },
        },
        {
            "id": "archive4k",
            "title": "Архив 4K",
            "note": "До 3840, x265, CRF 20, MKV",
            "values": {
                "scale_mode": "long_edge", "long_edge": 3840, "codec": "libx265",
                "crf": 20, "crf_mode": "crf", "x264_preset": "slow", "container": "mkv",
            },
        },
        {
            "id": "anime4",
            "title": "Аниме ×4",
            "note": "animevideo v3 ×4, CRF 16",
            "values": {
                "scale_mode": "percent", "percent": 400, "model": "realesr-animevideov3-x4",
                "codec": "libx264", "crf": 16, "crf_mode": "crf", "x264_preset": "slow",
                "container": "mp4",
            },
        },
        {
            "id": "denoise",
            "title": "Старая запись",
            "note": "Денойз-модель, ×2, CRF 20",
            "values": {
                "scale_mode": "percent", "percent": 200, "model": "realesr-general-wdn-x4v3",
                "codec": "libx264", "crf": 20, "crf_mode": "crf", "x264_preset": "medium",
                "container": "mp4",
            },
        },
    ]


@app.post("/api/presets")
async def save_preset(payload: dict) -> dict:
    title = (payload.get("title") or "").strip()
    if not title:
        return json_error("Пустое имя пресета", 400)
    items = config.load_user_presets()
    items = [p for p in items if p.get("title") != title]
    items.append({
        "id": uuid.uuid4().hex[:8],
        "title": title,
        "values": payload.get("values") or {},
        "created": time.time(),
    })
    return {"user": config.save_user_presets(items)}


@app.delete("/api/presets/{preset_id}")
def delete_preset(preset_id: str) -> dict:
    items = config.load_user_presets()
    items = [p for p in items if p.get("id") != preset_id]
    return {"user": config.save_user_presets(items)}


@app.get("/api/presets/export")
def export_presets() -> Response:
    data = json.dumps(config.load_user_presets(), ensure_ascii=False, indent=2)
    return Response(
        data,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=pedro-presets.json"},
    )


@app.get("/api/command")
def command(token: str, options: str = "{}") -> dict:
    import json as _json

    try:
        opts = _json.loads(options)
    except ValueError:
        opts = {}
    try:
        src = _find_source(token)
        meta = ffmpeg.probe(src)
    except (HTTPException, RuntimeError) as exc:
        detail = getattr(exc, "detail", str(exc))
        return json_error(str(detail), 400)

    merged = config.load_settings()
    merged.update(opts)
    try:
        engine = upscale.resolve_engine(merged)
        model = upscale.pick_model(merged, engine)
        target = upscale.compute_target(meta, merged, model.get("scale"))
    except RuntimeError as exc:
        return json_error(str(exc), 400)

    stem = Path(meta.get("name") or src.name).stem
    dst = config.OUT_DIR / "{}_up{}x.mp4".format(stem, "{:g}".format(target["exact_factor"]))
    cmd = upscale.build_command(meta, merged, engine, model, target, src, dst)
    return {
        "command": " ".join(cmd),
        "upscaler": " ".join(_upscaler_command(merged, model, engine, target, src, dst)),
    }


def _upscaler_command(opts: dict, model: dict, engine: str, target: dict, src: Path, dst: Path) -> list[str]:
    m_engine = model["engines"][0]
    exe = config.ENGINES[m_engine]["exe"]
    cmd = [str(exe), "-i", "frames/", "-o", "frames_up/", "-s", str(target["model_scale"])]
    if m_engine == "gpu":
        cmd += ["-n", model["id"]]
    elif m_engine == "srmd":
        cmd += ["-n", str(int(model.get("noise", -1)))]
    cmd += ["-g", str(-1 if m_engine == "cpu" else int(opts.get("gpu_id", 0) or 0))]
    if int(opts.get("tile") or 0) > 0:
        cmd += ["-t", str(int(opts["tile"]))]
    if opts.get("tta"):
        cmd += ["-x"]
    return cmd


@app.get("/api/estimate")
def estimate(token: str, options: str = "{}") -> dict:
    import json as _json

    try:
        opts = _json.loads(options)
    except ValueError:
        opts = {}
    try:
        src = _find_source(token)
        meta = ffmpeg.probe(src)
    except (HTTPException, RuntimeError) as exc:
        detail = getattr(exc, "detail", str(exc))
        return json_error(str(detail), 400)

    merged = config.load_settings()
    merged.update(opts)
    try:
        engine = upscale.resolve_engine(merged)
        model = upscale.pick_model(merged, engine)
        target = upscale.compute_target(meta, merged, model.get("scale"))
    except RuntimeError as exc:
        return json_error(str(exc), 400)

    frames = int(round((meta.get("fps") or 25) * (meta.get("duration") or 0)))
    # Оценка по замерам: RX 550X ~1.4 с/кадр на 720p в CPU-режиме, Vulkan быстрее.
    engine0 = model["engines"][0]
    # Замерено: srmd_x2 на RX 550X ~2.4 раза быстрее realesrgan на том же кадре.
    per_frame = {"cpu": 1.4, "srmd": 0.22}.get(engine0, 0.55)
    out_mb = frames * target["target_w"] * target["target_h"] * 0.12 / 1024 / 1024
    return {
        "frames": frames,
        "target": target,
        "engine": model["engines"][0],
        "model": model["id"],
        "seconds": int(frames * per_frame),
        "out_mb": int(out_mb),
    }


class NoCacheStatic(StaticFiles):
    """Статика интерфейса без кэширования: браузер всегда берёт свежие файлы.

    Отделы истории вроде «кнопка мёртвая из-за закэшированного HTML с disabled»
    больше невозможны — каждая загрузка страницы идёт с диска.
    """

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response


app.mount("/", NoCacheStatic(directory=str(config.WEB_DIR), html=True), name="web")


def run() -> None:
    import uvicorn

    # Объект app, а не строка: при `python server/main.py` пакет server не в пути.
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    run()
