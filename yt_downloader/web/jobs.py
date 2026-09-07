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
from ..exceptions import TranscriptionCancelled
from ..transcription.config import TranscriptionConfig

# Sentinel pushed onto a job's queue when no further events will arrive.
_DONE = object()

# Statuses after which a job emits no further events.
_TERMINAL = {"completed", "error", "cancelled"}

# How long a job waits for a free slot before giving up. Long enough to ride
# out a busy spell, short enough that a client is not held forever.
_QUEUE_TIMEOUT = 1800.0


@dataclass
class Job:
    """One download, its live state, and the queue feeding its SSE stream."""

    id: str
    # Which browser session queued this. Every job endpoint checks it, so one
    # visitor can never see, cancel or download another visitor's work.
    owner: str
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


class QuotaExceeded(Exception):
    """The session has queued more jobs than its allowance."""


class JobManager:
    """Creates, tracks and cancels download jobs.

    Concurrency is capped on purpose. yt-dlp is I/O bound and cheap, but a
    Whisper transcription pins a CPU core for minutes; without a ceiling a
    handful of visitors would take the whole machine down.
    """

    def __init__(
            self,
            max_jobs: int = 200,
            max_concurrent: int = 3,
            max_concurrent_transcriptions: int = 1,
            max_jobs_per_session_hour: Optional[int] = None,
    ):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._max_jobs = max_jobs
        self._slots = threading.Semaphore(max_concurrent)
        self._transcribe_slots = threading.Semaphore(max_concurrent_transcriptions)
        self._max_per_session_hour = max_jobs_per_session_hour

    def get(self, job_id: str, owner: Optional[str] = None) -> Optional[Job]:
        """Fetch a job. With `owner`, only that session's job is returned."""
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return None
        if owner is not None and job.owner != owner:
            # Indistinguishable from "no such job", so ids cannot be probed.
            return None
        return job

    def all(self, owner: Optional[str] = None) -> list[Job]:
        with self._lock:
            found = [
                j for j in self._jobs.values()
                if owner is None or j.owner == owner
            ]
        return sorted(found, key=lambda j: j.created_at, reverse=True)

    def _check_quota(self, owner: str) -> None:
        if self._max_per_session_hour is None:
            return
        cutoff = time.time() - 3600
        with self._lock:
            recent = sum(
                1 for j in self._jobs.values()
                if j.owner == owner and j.created_at > cutoff
            )
        if recent >= self._max_per_session_hour:
            raise QuotaExceeded(
                f"Limit of {self._max_per_session_hour} jobs per hour reached. "
                "Try again later."
            )

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
            owner: str,
            url: str,
            output_path: str,
            config: DownloaderConfig,
            transcription: Optional[TranscriptionConfig] = None,
    ) -> Job:
        self._check_quota(owner)
        return self._start(Job(
            id=uuid.uuid4().hex[:12],
            owner=owner,
            url=url,
            output_path=output_path,
            config=config,
            transcription=transcription,
        ))

    def create_upload(
            self,
            owner: str,
            local_path: str,
            source_name: str,
            transcription: TranscriptionConfig,
            output_path: str,
    ) -> Job:
        """A transcription-only job for a file the user uploaded.

        `output_path` is where the transcript lands and must be outside the
        upload's own directory, which is deleted once the job finishes.
        """
        self._check_quota(owner)
        return self._start(Job(
            id=uuid.uuid4().hex[:12],
            owner=owner,
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

    def cancel(self, job_id: str, owner: Optional[str] = None) -> bool:
        job = self.get(job_id, owner)
        if job is None or job.status in _TERMINAL:
            return False
        if job._downloader is not None:
            job._downloader.cancel()
        job.status = "cancelling"
        self._emit(job)
        return True

    def active_paths(self) -> list[str]:
        """Files belonging to jobs still running, which a sweep must not touch."""
        with self._lock:
            return [
                j.filepath for j in self._jobs.values()
                if j.status not in _TERMINAL and j.filepath
            ]

    def clear_finished(self, owner: Optional[str] = None) -> int:
        with self._lock:
            done = [
                j for j in self._jobs.values()
                if j.status in _TERMINAL and (owner is None or j.owner == owner)
            ]
            for job in done:
                self._jobs.pop(job.id, None)
        return len(done)

    def _emit(self, job: Job) -> None:
        job._events.put(job.snapshot())

    def _finish(self, job: Job) -> None:
        job._events.put(_DONE)

    def _run(self, job: Job) -> None:
        """Worker thread: wait for a slot, then download and/or transcribe."""
        # Everything queues behind the global limit. The job stays visible as
        # "queued" meanwhile, so the page shows it waiting rather than nothing.
        acquired = self._slots.acquire(timeout=_QUEUE_TIMEOUT)
        if not acquired:
            job.status = "error"
            job.error = "The server is busy; try again in a few minutes."
            self._emit(job)
            self._finish(job)
            return

        try:
            if job.local_path is not None:
                self._run_upload(job)
            else:
                self._run_download(job)
        finally:
            self._slots.release()

    def _run_upload(self, job: Job) -> None:
        """An uploaded file skips the download stage entirely."""
        try:
            job.filepath = job.local_path
            self._transcribe(job)

            # Discard before marking the job terminal, never after: a snapshot
            # taken in between would advertise a media file that is about to
            # vanish, and the download button would 404.
            self._discard_upload(job)

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
            # Idempotent: also covers the exception path above.
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
                raise TranscriptionCancelled()
            if progress.status == "loading_model":
                job.status = "loading_model"
            elif progress.status == "transcribing":
                job.status = "transcribing"
                if progress.percent is not None:
                    job.percent = progress.percent
                job.transcript_seconds = progress.seconds_done
            self._emit(job)

        # Transcription gets its own, tighter limit: it is CPU bound, so more
        # than a couple at once makes every one of them slower and starves the
        # downloads sharing the machine.
        job.status = "queued"
        self._emit(job)
        if not self._transcribe_slots.acquire(timeout=_QUEUE_TIMEOUT):
            job.error = "Transcription queue is full; the media was downloaded."
            return

        try:
            self._run_transcription(job, on_transcribe)
        finally:
            self._transcribe_slots.release()

    def _run_transcription(self, job: Job, on_transcribe) -> None:
        from ..transcription import TranscriptionService

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
        except TranscriptionCancelled:
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


def is_terminal(status: str) -> bool:
    return status in _TERMINAL


class OutputPathRejected(ValueError):
    """The requested output directory falls outside the allowed root."""


def safe_output_path(raw: str, fallback: str, root: Optional[str] = None) -> Path:
    """Resolve a client-supplied output directory, confined to `root`.

    The web form lets the caller name a destination, so without a root any
    request could write anywhere the process can reach -- /etc, ~/.ssh, cron.
    With a root set, the resolved path must stay inside it.

    Args:
        raw: What the client asked for. Empty means `fallback`.
        fallback: Default destination when the client asked for nothing.
        root: Directory the result must live under. None disables the check,
            which is only appropriate for a purely local, single-user run.

    Raises:
        OutputPathRejected: The path resolves outside `root`.
    """
    candidate = Path(raw).expanduser() if raw else Path(fallback).expanduser()

    if root is None:
        return candidate.resolve()

    base = Path(root).expanduser().resolve()

    # With a root configured, "no preference" means the root itself. Falling
    # back to the relative default here would resolve it *inside* the root and
    # nest a directory that nobody asked for -- /data/videos/videos.
    if not raw:
        return base

    # resolve() collapses "..", so a traversal attempt cannot survive this.
    resolved = (base / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()

    if resolved != base and base not in resolved.parents:
        raise OutputPathRejected(
            f"Output directory must be inside {base}"
        )
    return resolved
