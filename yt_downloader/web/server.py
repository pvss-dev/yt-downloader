"""FastAPI server exposing the downloader to the browser UI."""

import asyncio
import json
import logging
import queue
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..config import DownloaderConfig
from ..downloader import VideoDownloader
from ..exceptions import DownloaderException
from .jobs import _DONE, JobManager, safe_output_path

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="YouTube Downloader", version="2.0.0")
jobs = JobManager()


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


@app.get("/api/health")
async def health() -> dict:
    import yt_dlp

    return {
        "status": "ok",
        "yt_dlp_version": yt_dlp.version.__version__,
        "default_output": str(Path(DownloaderConfig.default_output_dir).resolve()),
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
async def start_download(payload: DownloadRequest) -> dict:
    output = safe_output_path(payload.output_path, DownloaderConfig.default_output_dir)
    job = jobs.create(payload.url, str(output), payload.to_config())
    return job.snapshot()


@app.get("/api/jobs")
async def list_jobs() -> dict:
    return {"jobs": [job.snapshot() for job in jobs.all()]}


@app.delete("/api/jobs")
async def clear_jobs() -> dict:
    return {"cleared": jobs.clear_finished()}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict:
    if not jobs.cancel(job_id):
        raise HTTPException(status_code=404, detail="Job not found or already finished")
    return {"cancelled": True}


@app.get("/api/jobs/{job_id}/file")
async def download_file(job_id: str) -> FileResponse:
    """Serve the finished file so the browser can save it locally."""
    job = jobs.get(job_id)
    if job is None or not job.filepath:
        raise HTTPException(status_code=404, detail="File not available")

    path = Path(job.filepath)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File no longer on disk")

    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    """Server-sent events carrying live progress for one job."""
    job = jobs.get(job_id)
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


def run(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Entry point used by `python -m yt_downloader.web`."""
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%H:%M:%S",
    )
    print(f"\n  YouTube Downloader UI  ->  http://{host}:{port}\n")
    uvicorn.run(
        "yt_downloader.web.server:app" if reload else app,
        host=host,
        port=port,
        reload=reload,
        log_level="warning",
    )
