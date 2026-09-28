"""Очередь задач апскейла: состояние, прогресс, отмена."""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Callable, Optional

STAGES = {
    "queued": "в очереди",
    "probing": "анализ файла",
    "preparing": "подготовка",
    "decoding": "декодирование",
    "upscaling": "апскейл кадров",
    "encoding": "кодирование",
    "assembling": "сборка",
    "done": "готово",
    "failed": "ошибка",
    "cancelled": "отменено",
}


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, source: Path, options: dict, meta: dict):
        self.id = uuid.uuid4().hex[:12]
        self.source = source
        self.original_name = (meta or {}).get("name") or source.name
        self.options = options
        self.meta = meta
        self.stage = "queued"
        self.percent = 0.0
        self.frame = 0
        self.frames_total = int(meta.get("frames") or 0)
        self.message = STAGES["queued"]
        self.error: Optional[str] = None
        self.output: Optional[Path] = None
        self.output_meta: dict = {}
        self.logs: list[dict] = []
        self.created = time.time()
        self.started: Optional[float] = None
        self.finished: Optional[float] = None
        self.eta: Optional[float] = None
        self.upscaled: list[Path] = []
        self.segments: list[Path] = []
        self.tmp_dir: Optional[Path] = None
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._speed = 0.0
        self._frames_done = 0

    # --- состояние -------------------------------------------------------
    def set_stage(self, stage: str, message: str = "") -> None:
        with self._lock:
            self.stage = stage
            self.message = message or STAGES.get(stage, stage)
        if stage == "done":
            self.percent = 100.0

    def set_progress(self, frames_done: int, stage: str = "") -> None:
        with self._lock:
            self._frames_done = frames_done
            if stage:
                self.stage = stage
                self.message = STAGES.get(stage, stage)
            if self.frames_total > 0:
                self.percent = min(99.0, frames_done * 100.0 / self.frames_total)
            now = time.time()
            if self.started and frames_done > 0:
                rate = frames_done / max(1e-6, now - self.started)
                if rate > 0:
                    self._speed = rate
                    if self.frames_total > frames_done:
                        self.eta = (self.frames_total - frames_done) / rate

    @property
    def speed(self) -> float:
        return round(self._speed, 2)

    def log(self, text: str, level: str = "info") -> None:
        entry = {"t": time.time(), "level": level, "text": text}
        with self._lock:
            self.logs.append(entry)
            if len(self.logs) > 600:
                del self.logs[:200]

    # --- отмена ----------------------------------------------------------
    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check_cancel(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    # --- сериализация ----------------------------------------------------
    def public(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "stage": self.stage,
                "stage_label": STAGES.get(self.stage, self.stage),
                "message": self.message,
                "percent": round(self.percent, 1),
                "frame": self._frames_done,
                "frames_total": self.frames_total,
                "speed": self.speed,
                "eta": round(self.eta) if self.eta else None,
                "source": self.source.name,
                "output": self.output.name if self.output else None,
                "output_size": self.output.stat().st_size if self.output and self.output.exists() else 0,
                "output_meta": self.output_meta,
                "error": self.error,
                "created": self.created,
                "started": self.started,
                "finished": self.finished,
                "cancelled": self.cancelled,
                "options": self.options,
            }


class JobManager:
    def __init__(self, limit: int = 2):
        self.jobs: dict[str, Job] = {}
        self.order: list[str] = []
        self.lock = threading.Lock()
        self.sem = threading.BoundedSemaphore(limit)
        self.limit = limit

    def create(self, source: Path, options: dict, meta: dict) -> Job:
        job = Job(source, options, meta)
        with self.lock:
            self.jobs[job.id] = job
            self.order.append(job.id)
            while len(self.order) > 40:
                old = self.order.pop(0)
                stale = self.jobs.get(old)
                if stale and stale.stage not in ("queued",) and not stale.output:
                    self.jobs.pop(old, None)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self.lock:
            return self.jobs.get(job_id)

    def list(self) -> list[dict]:
        with self.lock:
            return [self.jobs[i].public() for i in reversed(self.order) if i in self.jobs]

    def active(self) -> list[Job]:
        with self.lock:
            return [
                self.jobs[i]
                for i in self.order
                if i in self.jobs and self.jobs[i].stage in ("queued", "probing", "preparing", "decoding", "upscaling", "encoding", "assembling")
            ]

    def run(self, job: Job, worker: Callable[[Job], Path]) -> None:
        self.sem.acquire()
        try:
            if job.cancelled:
                job.set_stage("cancelled")
                return
            job.started = time.time()
            job.set_stage("preparing")
            output = worker(job)
            job.output = output
            job.finished = time.time()
            job.set_stage("done")
            job.percent = 100.0
            job.eta = 0
        except Cancelled:
            job.finished = time.time()
            job.set_stage("cancelled")
            job.log("Задача отменена", "warn")
        except Exception as exc:  # noqa: BLE001 - ошибку показываем в UI
            job.finished = time.time()
            job.error = str(exc)
            job.set_stage("failed")
            job.log("Ошибка: {}".format(exc), "error")
            job.log(traceback.format_exc().strip().splitlines()[-1], "error")
        finally:
            self.sem.release()

    def cleanup(self, job: Job) -> None:
        import shutil

        if job.tmp_dir and job.tmp_dir.exists():
            shutil.rmtree(job.tmp_dir, ignore_errors=True)
            job.tmp_dir = None
        for path in list(job.segments):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        job.segments = []


MANAGER = JobManager()
