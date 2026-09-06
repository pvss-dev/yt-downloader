"""FastAPI server exposing the downloader to the browser UI."""

import asyncio
import json
import logging
import os
import queue
import secrets
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..config import DownloaderConfig
from ..downloader import VideoDownloader
from ..exceptions import DownloaderException
from ..retention import RetentionPolicy, RetentionScheduler
from ..transcription.config import WHISPER_MODELS, TranscriptionConfig
from ..transcription.transcriber import Transcriber
from .jobs import (
    _DONE,
    JobManager,
    OutputPathRejected,
    QuotaExceeded,
    safe_output_path,
)

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# When set, every client-supplied destination must resolve inside this
# directory. Deployments should always set it; a bare local run may not.
OUTPUT_ROOT: Optional[str] = os.getenv("YTDL_OUTPUT_ROOT") or None

# Public deployments must not let a visitor choose where files land, no matter
# what the form sends. Local runs keep the field working.
PUBLIC_MODE = os.getenv("YTDL_PUBLIC", "").lower() in ("1", "true", "yes")

SESSION_COOKIE = "ytdl_session"

# Media the browser uploads lands here until its job finishes.
UPLOAD_ROOT = Path(tempfile.gettempdir()) / "yt-downloader-uploads"

# Refuse implausible uploads outright rather than filling the disk.
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024  # 4 GB
CHUNK = 1024 * 1024

app = FastAPI(title="YouTube Downloader", version="2.0.0")

jobs = JobManager(
    max_concurrent=int(os.getenv("YTDL_MAX_CONCURRENT", "3")),
    max_concurrent_transcriptions=int(os.getenv("YTDL_MAX_TRANSCRIPTIONS", "1")),
    max_jobs_per_session_hour=(
        int(os.getenv("YTDL_JOBS_PER_HOUR", "20")) if PUBLIC_MODE else None
    ),
)


def session_of(request: Request) -> str:
    """The caller's session id, from the cookie set by the middleware."""
    return request.cookies.get(SESSION_COOKIE) or request.state.session


@app.middleware("http")
async def attach_session(request: Request, call_next):
    """Give every visitor an opaque id, so jobs can be scoped to them.

    This is ownership, not authentication: it stops one visitor seeing or
    cancelling another's jobs. It does not identify anybody.
    """
    existing = request.cookies.get(SESSION_COOKIE)
    request.state.session = existing or secrets.token_urlsafe(24)

    response = await call_next(request)

    if not existing:
        response.set_cookie(
            SESSION_COOKIE,
            request.state.session,
            max_age=60 * 60 * 24 * 30,
            httponly=True,
            samesite="lax",
        )
    return response

# Set by run() when the operator asks for automatic cleanup; stays None
# otherwise, so no file is ever deleted unless requested.
retention: Optional[RetentionScheduler] = None


class InfoRequest(BaseModel):
    url: str = Field(min_length=1)


class DownloadRequest(BaseModel):
    url: str = Field(min_length=1)
    output_path: str = ""
    max_height: Optional[int] = None
    audio_only: bool = False
    audio_format: str = "mp3"
    playlist: bool = False
    subtitles: bool = False
    thumbnail: bool = False
    overwrite: bool = False
    transcribe: bool = False
    whisper_model: str = "small"
    language: Optional[str] = "pt"

    def to_config(self) -> DownloaderConfig:
        return DownloaderConfig(
            max_height=self.max_height,
            audio_only=self.audio_only,
            audio_format=self.audio_format,
            playlist=self.playlist,
            write_subtitles=self.subtitles,
            embed_thumbnail=self.thumbnail,
            overwrite_files=self.overwrite,
        )

    def to_transcription_config(self) -> Optional[TranscriptionConfig]:
        if not self.transcribe:
            return None
        return TranscriptionConfig(
            whisper_model=self.whisper_model,
            # An empty language means "let Whisper detect it".
            language=self.language or None,
        )


@app.get("/api/health")
async def health() -> dict:
    import yt_dlp

    return {
        "status": "ok",
        "yt_dlp_version": yt_dlp.version.__version__,
        "default_output": str(Path(DownloaderConfig.default_output_dir).resolve()),
        "transcription_available": Transcriber.is_available(),
        "whisper_models": list(WHISPER_MODELS),
        "retention": retention.policy.describe() if retention else None,
        "output_root": None if PUBLIC_MODE else OUTPUT_ROOT,
        "public_mode": PUBLIC_MODE,
    }


@app.post("/api/info")
async def video_info(payload: InfoRequest) -> dict:
    """Fetch metadata for the preview card, without downloading."""
    def fetch():
        downloader = VideoDownloader(config=DownloaderConfig(playlist=True))
        # Flat: the preview only needs title/uploader/count, and a full
        # playlist extraction would block the card for ~30s.
        return downloader.get_info(payload.url, flat=True)

    try:
        # yt-dlp blocks; keep the event loop free for the SSE streams.
        info = await asyncio.to_thread(fetch)
    except DownloaderException as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unexpected error fetching info")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e

    return {
        "id": info.id,
        "title": info.title,
        "uploader": info.uploader,
        "duration": info.duration_display,
        "thumbnail": info.thumbnail,
        "webpage_url": info.webpage_url,
        "is_playlist": info.is_playlist,
        "entry_count": info.entry_count,
        "available_heights": info.available_heights,
    }


@app.post("/api/download")
async def start_download(payload: DownloadRequest, request: Request) -> dict:
    if payload.transcribe and not Transcriber.is_available():
        raise HTTPException(
            status_code=400,
            detail=(
                "Transcription is not installed. Run: pip install -e \".[transcribe]\""
            ),
        )

    try:
        transcription = payload.to_transcription_config()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # In public mode the server decides where files go, full stop.
    requested = "" if PUBLIC_MODE else payload.output_path
    try:
        output = safe_output_path(
            requested, DownloaderConfig.default_output_dir, OUTPUT_ROOT
        )
    except OutputPathRejected as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        job = jobs.create(
            session_of(request), payload.url, str(output),
            payload.to_config(), transcription,
        )
    except QuotaExceeded as e:
        raise HTTPException(status_code=429, detail=str(e)) from e

    return job.snapshot()


@app.post("/api/upload")
async def upload_and_transcribe(
        request: Request,
        file: UploadFile = File(...),
        whisper_model: str = Form("small"),
        language: str = Form("pt"),
        output_path: str = Form(""),
) -> dict:
    """Accept a media file from the browser and transcribe it.

    No download stage: the bytes arrive over HTTP, so the job goes straight to
    Whisper.
    """
    if not Transcriber.is_available():
        raise HTTPException(
            status_code=400,
            detail='Transcription is not installed. Run: pip install -e ".[transcribe]"',
        )

    try:
        transcription = TranscriptionConfig(
            whisper_model=whisper_model,
            language=language or None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Keep only the basename: a browser may send a path, and "../" in it must
    # never escape the upload directory.
    source_name = Path(file.filename or "upload").name
    if not source_name or source_name in (".", ".."):
        source_name = "upload"

    workdir = UPLOAD_ROOT / uuid.uuid4().hex[:12]
    workdir.mkdir(parents=True, exist_ok=True)
    target = workdir / source_name

    written = 0
    try:
        with target.open("wb") as sink:
            while chunk := await file.read(CHUNK):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File is larger than {MAX_UPLOAD_BYTES // (1024**3)} GB",
                    )
                sink.write(chunk)
    except HTTPException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    except OSError as e:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Could not store upload: {e}") from e
    finally:
        await file.close()

    if written == 0:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="The uploaded file is empty")

    try:
        destination = safe_output_path(
            "" if PUBLIC_MODE else output_path,
            DownloaderConfig.default_output_dir,
            OUTPUT_ROOT,
        )
    except OutputPathRejected as e:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(e)) from e

    destination.mkdir(parents=True, exist_ok=True)

    try:
        job = jobs.create_upload(
            owner=session_of(request),
            local_path=str(target),
            source_name=source_name,
            transcription=transcription,
            output_path=str(destination),
        )
    except QuotaExceeded as e:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(status_code=429, detail=str(e)) from e

    return job.snapshot()


@app.post("/api/retention/sweep")
async def run_retention_sweep(dry_run: bool = True) -> dict:
    """Run the cleanup now. Defaults to a dry run."""
    if PUBLIC_MODE:
        # Deleting other people's files is not a visitor's call to make.
        raise HTTPException(status_code=404, detail="Not available")
    if retention is None:
        raise HTTPException(
            status_code=400,
            detail="Retention is off. Start the server with --retention-days/--keep/--max-gb.",
        )

    result = await asyncio.to_thread(retention.run_once, dry_run)
    return {
        "dry_run": result.dry_run,
        "deleted": [str(p) for p in result.deleted],
        "freed_bytes": result.freed_bytes,
        "freed": result.freed_display,
        "kept": result.kept,
        "errors": result.errors,
        "summary": result.summary(),
    }


@app.get("/api/jobs")
async def list_jobs(request: Request) -> dict:
    return {"jobs": [job.snapshot() for job in jobs.all(session_of(request))]}


@app.delete("/api/jobs")
async def clear_jobs(request: Request) -> dict:
    return {"cleared": jobs.clear_finished(session_of(request))}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request) -> dict:
    if not jobs.cancel(job_id, session_of(request)):
        raise HTTPException(status_code=404, detail="Job not found or already finished")
    return {"cancelled": True}


@app.get("/api/jobs/{job_id}/file")
async def download_file(job_id: str, request: Request) -> FileResponse:
    """Serve the finished file so the browser can save it locally."""
    job = jobs.get(job_id, session_of(request))
    if job is None or not job.filepath:
        raise HTTPException(status_code=404, detail="File not available")

    path = Path(job.filepath)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File no longer on disk")

    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@app.get("/api/jobs/{job_id}/transcript")
async def download_transcript(job_id: str, request: Request) -> FileResponse:
    """Serve the finished transcript as a .txt download."""
    job = jobs.get(job_id, session_of(request))
    if job is None or not job.transcript_path:
        raise HTTPException(status_code=404, detail="Transcript not available")

    path = Path(job.transcript_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Transcript no longer on disk")

    return FileResponse(path, filename=path.name, media_type="text/plain; charset=utf-8")


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    """Server-sent events carrying live progress for one job."""
    job = jobs.get(job_id, session_of(request))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_stream():
        # Send current state immediately so a late subscriber isn't blank.
        yield f"data: {json.dumps(job.snapshot())}\n\n"

        while True:
            try:
                event = await asyncio.to_thread(job._events.get, True, 15)
            except queue.Empty:
                # Comment frame keeps proxies from closing an idle connection.
                yield ": keepalive\n\n"
                continue

            if event is _DONE:
                yield f"event: done\ndata: {json.dumps(job.snapshot())}\n\n"
                return

            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def run(
        host: str = "127.0.0.1",
        port: int = 8000,
        reload: bool = False,
        retention_policy: Optional[RetentionPolicy] = None,
        retention_dir: Optional[str] = None,
        retention_interval_minutes: float = 60.0,
) -> None:
    """Entry point used by `python -m yt_downloader.web`."""
    import uvicorn

    global retention

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%H:%M:%S",
    )
    if retention_policy is not None and retention_policy.is_active:
        retention = RetentionScheduler(
            directory=retention_dir or DownloaderConfig.default_output_dir,
            policy=retention_policy,
            interval_seconds=retention_interval_minutes * 60,
            protected_paths=jobs.active_paths,
        )
        retention.start()

    print(f"\n  YouTube Downloader UI  ->  http://{host}:{port}\n")
    uvicorn.run(
        "yt_downloader.web.server:app" if reload else app,
        host=host,
        port=port,
        reload=reload,
        log_level="warning",
    )
