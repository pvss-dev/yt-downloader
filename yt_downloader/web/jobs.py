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
from ..transcription.config import TranscriptionConfig

# Sentinel pushed onto a job's queue when no further events will arrive.
_DONE = object()

# Statuses after which a job emits no further events.
_TERMINAL = {"completed", "error", "cancelled"}


@dataclass
class Job:
    """One download, its live state, and the queue feeding its SSE stream."""

    id: str
    # Exactly one of these is set: `url` for a download, `local_path` for a
    # file the user dropped on the page.
    url: Optional[str]
    output_path: str
    config: DownloaderConfig
    local_path: Optional[str] = None
    source_name: Optional[str] = None
    transcription: Optional[TranscriptionConfig] = None
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
    transcript_path: Optional[str] = None
    transcript_seconds: Optional[float] = None
    transcript_preview: Optional[str] = None
    detected_language: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    _events: queue.Queue = field(default_factory=queue.Queue, repr=False)
    _downloader: Optional[VideoDownloader] = field(default=None, repr=False)

    def snapshot(self) -> dict[str, Any]:
        """Serializable view of the job for the client."""
        return {
            "id": self.id,
            "url": self.url,
            "source_name": self.source_name,
            "is_upload": self.local_path is not None,
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
            "transcribe": self.transcription is not None,
            "transcript_path": self.transcript_path,
            "transcript_seconds": self.transcript_seconds,
            "transcript_preview": self.transcript_preview,
            "detected_language": self.detected_language,
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

    def create(
            self,
            url: str,
            output_path: str,
            config: DownloaderConfig,
            transcription: Optional[TranscriptionConfig] = None,
    ) -> Job:
        return self._start(Job(
            id=uuid.uuid4().hex[:12],
            url=url,
            output_path=output_path,
            config=config,
            transcription=transcription,
        ))

    def create_upload(
            self,
            local_path: str,
            source_name: str,
            transcription: TranscriptionConfig,
            output_path: str,
    ) -> Job:
        """A transcription-only job for a file the user uploaded.

        `output_path` is where the transcript lands and must be outside the
        upload's own directory, which is deleted once the job finishes.
        """
        return self._start(Job(
            id=uuid.uuid4().hex[:12],
            url=None,
            output_path=output_path,
            config=DownloaderConfig(),
            local_path=local_path,
            source_name=source_name,
            transcription=transcription,
            title=source_name,
        ))

    def _start(self, job: Job) -> Job:
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
        """Worker thread: download and/or transcribe, emitting as it goes."""
        if job.local_path is not None:
            self._run_upload(job)
            return

        self._run_download(job)

    def _run_upload(self, job: Job) -> None:
        """An uploaded file skips the download stage entirely."""
        try:
            job.filepath = job.local_path
            self._transcribe(job)

            if job.status == "cancelling":
                job.status = "cancelled"
                job.error = "Cancelled by user"
            elif job.error:
                job.status = "error"
            else:
                job.status = "completed"
                job.percent = 100.0

        except Exception as e:
            job.status = "error"
            job.error = f"{type(e).__name__}: {e}"
        finally:
            # The user already has the original on their machine; keeping a
            # second copy on the server serves nobody.
            self._discard_upload(job)
            self._emit(job)
            self._finish(job)

    @staticmethod
    def _discard_upload(job: Job) -> None:
        if not job.local_path:
            return
        try:
            upload = Path(job.local_path)
            upload.unlink(missing_ok=True)
            # create_upload gives each upload its own directory.
            if upload.parent.is_dir() and not any(upload.parent.iterdir()):
                upload.parent.rmdir()
        except OSError:
            pass
        finally:
            job.filepath = None

    def _run_download(self, job: Job) -> None:
        """Fetch metadata, then download, then optionally transcribe."""

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
                job.filepath = str(result.filepath) if result.filepath else None
                if result.info:
                    job.title = result.info.title

                if job.transcription is not None:
                    self._transcribe(job)

                if job.status == "cancelling":
                    # Cancelled mid-transcription; the media file still landed.
                    job.status = "cancelled"
                    job.error = "Cancelled by user"
                else:
                    job.status = "completed"
                    job.percent = 100.0
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


    def _transcribe(self, job: Job) -> None:
        """Second stage: turn the downloaded media into a transcript.

        Runs against the file already on disk, so enabling transcription costs
        one download rather than two.
        """
        from ..transcription import TranscriptionService
        from ..transcription.transcriber import TranscriptionProgress

        if not job.filepath:
            job.error = "Cannot transcribe: downloaded file path is unknown"
            return

        def on_transcribe(progress: "TranscriptionProgress") -> None:
            if self._cancelled(job):
                raise _TranscriptionCancelled()
            if progress.status == "loading_model":
                job.status = "loading_model"
            elif progress.status == "transcribing":
                job.status = "transcribing"
                if progress.percent is not None:
                    job.percent = progress.percent
                job.transcript_seconds = progress.seconds_done
            self._emit(job)

        job.status = "loading_model"
        job.percent = 0.0
        self._emit(job)

        service = TranscriptionService(job.transcription, on_transcribe_progress=on_transcribe)

        target = None
        if job.local_path is not None:
            # Default would put the .txt beside the upload, inside the temp
            # directory that gets removed when the job ends.
            stem = Path(job.source_name or job.filepath).stem
            target = str(Path(job.output_path) / f"{stem}.txt")

        try:
            outcome = service.process(job.filepath, target)
        except _TranscriptionCancelled:
            job.status = "cancelling"
            return

        if not outcome.success:
            # The media downloaded fine; surface the transcript failure without
            # throwing away the file the user already has.
            job.error = f"Transcription failed: {outcome.error}"
            return

        job.transcript_path = str(outcome.transcript_path)
        if outcome.result:
            job.detected_language = outcome.result.language
            preview = outcome.result.text.strip().replace("\n", " ")
            job.transcript_preview = preview[:300] + ("..." if len(preview) > 300 else "")

    @staticmethod
    def _cancelled(job: Job) -> bool:
        # JobManager.cancel() sets this before anything else, so it is the one
        # signal both stages need to watch.
        return job.status == "cancelling"


class _TranscriptionCancelled(Exception):
    """Unwinds out of the Whisper progress hook when the user cancels."""


def is_terminal(status: str) -> bool:
    return status in _TERMINAL


def safe_output_path(raw: str, fallback: str) -> Path:
    """Expand and validate a user-supplied output directory."""
    candidate = Path(raw).expanduser() if raw else Path(fallback).expanduser()
    return candidate.resolve()
