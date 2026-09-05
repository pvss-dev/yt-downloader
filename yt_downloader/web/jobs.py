"""In-memory job queue backing the web UI.

yt-dlp is fully synchronous, so each download runs on its own worker thread and
publishes normalized events into a per-job queue. The HTTP layer drains those
queues over SSE.
"""

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..config import DownloaderConfig
from ..downloader import Progress, VideoDownloader

# Sentinel pushed onto a job's queue when no further events will arrive.
_DONE = object()

# Statuses after which a job emits no further events.
_TERMINAL = {"completed", "error", "cancelled"}


@dataclass
class Job:
    """One download, its live state, and the queue feeding its SSE stream."""

    id: str
    url: str
    output_path: str
    config: DownloaderConfig
    status: str = "queued"
    percent: float = 0.0
    speed: Optional[float] = None
    eta: Optional[int] = None
    downloaded_bytes: int = 0
    total_bytes: Optional[int] = None
    title: Optional[str] = None
    uploader: Optional[str] = None
    thumbnail: Optional[str] = None
    duration: Optional[str] = None
    filepath: Optional[str] = None
    error: Optional[str] = None
    stream: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    _events: queue.Queue = field(default_factory=queue.Queue, repr=False)
    _downloader: Optional[VideoDownloader] = field(default=None, repr=False)

    def snapshot(self) -> dict[str, Any]:
        """Serializable view of the job for the client."""
        return {
            "id": self.id,
            "url": self.url,
            "status": self.status,
            "percent": round(self.percent, 1),
            "speed": self.speed,
            "eta": self.eta,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "title": self.title,
            "uploader": self.uploader,
            "thumbnail": self.thumbnail,
            "duration": self.duration,
            "filepath": self.filepath,
            "error": self.error,
            "stream": self.stream,
        }


class JobManager:
    """Creates, tracks and cancels download jobs."""

    def __init__(self, max_jobs: int = 200):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._max_jobs = max_jobs

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def all(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def _register(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job
            # Keep the map bounded; drop the oldest finished jobs first so a
            # long-running session doesn't grow without limit.
            if len(self._jobs) > self._max_jobs:
                finished = sorted(
                    (j for j in self._jobs.values() if j.status in _TERMINAL),
                    key=lambda j: j.created_at,
                )
                for old in finished[: len(self._jobs) - self._max_jobs]:
                    self._jobs.pop(old.id, None)

    def create(self, url: str, output_path: str, config: DownloaderConfig) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:12],
            url=url,
            output_path=output_path,
            config=config,
        )
        self._register(job)

        thread = threading.Thread(target=self._run, args=(job,), daemon=True)
        thread.start()
        return job

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or job.status in _TERMINAL:
            return False
        if job._downloader is not None:
            job._downloader.cancel()
        job.status = "cancelling"
        self._emit(job)
        return True

    def clear_finished(self) -> int:
        with self._lock:
            done = [j for j in self._jobs.values() if j.status in _TERMINAL]
            for job in done:
                self._jobs.pop(job.id, None)
        return len(done)

    def _emit(self, job: Job) -> None:
        job._events.put(job.snapshot())

    def _finish(self, job: Job) -> None:
        job._events.put(_DONE)

    def _run(self, job: Job) -> None:
        """Worker thread: fetch metadata, then download, emitting as it goes."""

        def on_progress(progress: Progress) -> None:
            # Metadata rides along on the progress events, so the job never
            # pays for a second extraction just to fill in the card.
            if progress.info and not job.title:
                job.title = progress.info.title
                job.uploader = progress.info.uploader
                job.thumbnail = progress.info.thumbnail
                job.duration = progress.info.duration_display

            overall = progress.overall_percent
            if overall is not None:
                job.percent = overall

            if progress.status == "downloading":
                job.status = "downloading"
                job.speed = progress.speed
                job.eta = progress.eta
                job.downloaded_bytes = progress.downloaded_bytes
                job.total_bytes = progress.total_bytes
                job.stream = f"{progress.stream_index}/{progress.stream_total}"
            elif progress.status == "processing":
                job.status = "processing"
                job.speed = None
                job.eta = None

            self._emit(job)

        try:
            downloader = VideoDownloader(
                output_path=job.output_path,
                config=job.config,
                on_progress=on_progress,
            )
            job._downloader = downloader

            job.status = "fetching"
            self._emit(job)

            result = downloader.download(job.url)

            if result.success:
                job.status = "completed"
                job.percent = 100.0
                job.filepath = str(result.filepath) if result.filepath else None
                if result.info:
                    job.title = result.info.title
            elif job.status == "cancelling":
                job.status = "cancelled"
                job.error = "Cancelled by user"
            else:
                job.status = "error"
                job.error = result.error or "Download failed"

        except Exception as e:
            job.status = "error"
            job.error = f"{type(e).__name__}: {e}"
        finally:
            job.speed = None
            job.eta = None
            self._emit(job)
            self._finish(job)


def is_terminal(status: str) -> bool:
    return status in _TERMINAL


def safe_output_path(raw: str, fallback: str) -> Path:
    """Expand and validate a user-supplied output directory."""
    candidate = Path(raw).expanduser() if raw else Path(fallback).expanduser()
    return candidate.resolve()
