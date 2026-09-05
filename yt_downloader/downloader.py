import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError as YtDlpDownloadError

from .config import DownloaderConfig
from .exceptions import DirectoryError, DownloadError

logger = logging.getLogger(__name__)

ProgressCallback = Callable[["Progress"], None]


class DownloadCancelled(DownloadError):
    """Raised inside the progress hook to abort an in-flight download."""


@dataclass
class Progress:
    """A normalized snapshot of an in-flight download."""

    status: str
    downloaded_bytes: int = 0
    total_bytes: Optional[int] = None
    speed: Optional[float] = None
    eta: Optional[int] = None
    filename: Optional[str] = None
    fragment_index: Optional[int] = None
    fragment_count: Optional[int] = None
    # A merged download fetches video and audio as separate streams, each
    # running 0->100%. These place the current stream in the overall job.
    stream_index: int = 1
    stream_total: int = 1
    postprocessor: Optional[str] = None
    info: Optional["VideoInfo"] = None

    @property
    def percent(self) -> Optional[float]:
        """Progress of the current stream alone."""
        if not self.total_bytes:
            return None
        return min(100.0, self.downloaded_bytes / self.total_bytes * 100)

    @property
    def overall_percent(self) -> Optional[float]:
        """Progress across every stream of this download.

        Each stream gets an equal slice, so the bar advances monotonically
        instead of snapping back to zero when the audio stream starts.
        """
        slice_size = 100.0 / max(1, self.stream_total)

        if self.status == "processing":
            return 100.0
        if self.status == "finished":
            return min(100.0, self.stream_index * slice_size)

        fraction = self.percent
        if fraction is None:
            return None
        return min(100.0, (self.stream_index - 1) * slice_size + fraction * slice_size / 100)


@dataclass
class VideoInfo:
    """The subset of yt-dlp metadata this project actually uses."""

    id: str
    title: str
    uploader: str
    duration: Optional[int]
    thumbnail: Optional[str]
    webpage_url: str
    is_playlist: bool = False
    entry_count: int = 1
    available_heights: list[int] = field(default_factory=list)

    @property
    def duration_display(self) -> str:
        if not self.duration:
            return "--:--"
        hours, remainder = divmod(int(self.duration), 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    @classmethod
    def from_ydl(cls, info: dict[str, Any]) -> "VideoInfo":
        entries = info.get("entries")
        is_playlist = entries is not None

        if is_playlist:
            # `entries` may be a lazy generator; materialize it once so both the
            # count and the representative entry come from the same list.
            entries = [e for e in entries if e]
            first = entries[0] if entries else {}
        else:
            first = info

        heights = sorted({
            fmt["height"]
            for fmt in (first.get("formats") or [])
            if isinstance(fmt.get("height"), int)
        })

        # A flat playlist extraction has no top-level thumbnail; fall back to
        # the first entry, whose thumbnails only come as a list.
        thumbnail = info.get("thumbnail") or first.get("thumbnail")
        if not thumbnail:
            candidates = first.get("thumbnails") or info.get("thumbnails") or []
            if candidates:
                thumbnail = candidates[-1].get("url")

        return cls(
            id=info.get("id") or first.get("id") or "",
            title=info.get("title") or first.get("title") or "Unknown",
            uploader=(
                info.get("uploader")
                or info.get("channel")
                or first.get("uploader")
                or "Unknown"
            ),
            duration=first.get("duration") if is_playlist else info.get("duration"),
            thumbnail=thumbnail,
            webpage_url=info.get("webpage_url") or first.get("webpage_url") or "",
            is_playlist=is_playlist,
            entry_count=len(entries) if is_playlist else 1,
            available_heights=heights,
        )


@dataclass
class DownloadResult:
    """Outcome of a download, including where the file landed."""

    success: bool
    info: Optional[VideoInfo] = None
    filepath: Optional[Path] = None
    error: Optional[str] = None


class VideoDownloader:
    """Manage YouTube video downloads"""

    def __init__(
            self,
            output_path: Optional[str] = None,
            verbose: bool = False,
            config: Optional[DownloaderConfig] = None,
            on_progress: Optional[ProgressCallback] = None,
    ):
        """
        Initialize the downloader

        Args:
            output_path: Path to save videos
            verbose: Shows detailed information
            config: Custom configuration (optional)
            on_progress: Called with a Progress snapshot on each update
        """
        self.config = config or DownloaderConfig()
        self.output_path = Path(output_path or self.config.default_output_dir)
        self.verbose = verbose
        self.on_progress = on_progress
        self._cancelled = threading.Event()
        # Streams are identified by format_id as yt-dlp works through them.
        self._stream_ids: list[str] = []
        self._info: Optional[VideoInfo] = None
        self._announced_postprocessors: set[str] = set()

    def ensure_output_directory(self) -> None:
        """Creates the output directory if it does not exist.

        Called from download() rather than __init__ so that metadata-only uses
        (get_info) don't leave an empty folder behind.
        """
        try:
            self.output_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"Output directory: {self.output_path.absolute()}")
        except OSError as e:
            raise DirectoryError(f"Error creating directory {self.output_path}: {e}")

    def cancel(self) -> None:
        """Ask the running download to stop at the next progress update."""
        self._cancelled.set()

    def _expected_streams(self) -> int:
        """How many separate streams this format selector will fetch.

        A `bestvideo+bestaudio` selector downloads two files before merging.
        If yt-dlp falls back to a single combined format only one arrives, and
        the bar simply jumps forward at the end rather than backwards.
        """
        return 2 if "+" in self.config.build_format() else 1

    def _stream_position(self, d: dict[str, Any]) -> int:
        """1-based index of the stream this event belongs to."""
        info_dict = d.get("info_dict") or {}
        format_id = str(info_dict.get("format_id") or d.get("filename") or "")
        if format_id not in self._stream_ids:
            self._stream_ids.append(format_id)
        return self._stream_ids.index(format_id) + 1

    def _progress_hook(self, d: dict[str, Any]) -> None:
        """Normalize yt-dlp's raw hook payload and fan it out to the callback"""
        if self._cancelled.is_set():
            raise DownloadCancelled("Download cancelled by user")

        index = self._stream_position(d)

        # The per-stream info_dict already carries the full video metadata, so
        # callers get title/thumbnail without a second extraction pass.
        if self._info is None:
            info_dict = d.get("info_dict") or {}
            if info_dict.get("title"):
                self._info = VideoInfo.from_ydl(info_dict)

        progress = Progress(
            status=d.get("status", "unknown"),
            downloaded_bytes=d.get("downloaded_bytes") or 0,
            # `total_bytes` is absent for streams whose size is only estimated.
            total_bytes=d.get("total_bytes") or d.get("total_bytes_estimate"),
            speed=d.get("speed"),
            eta=d.get("eta"),
            filename=d.get("filename"),
            fragment_index=d.get("fragment_index"),
            fragment_count=d.get("fragment_count"),
            stream_index=index,
            stream_total=max(self._expected_streams(), len(self._stream_ids)),
            info=self._info,
        )

        if self.on_progress:
            self.on_progress(progress)

        if self.verbose and progress.status == "downloading":
            percent = progress.overall_percent
            shown = f"{percent:.1f}%" if percent is not None else "?"
            logger.info(f"Progress: {shown} | ETA: {progress.eta or '?'}s")

    def _postprocessor_hook(self, d: dict[str, Any]) -> None:
        """Report the merge/convert phase that follows the download."""
        if self._cancelled.is_set():
            raise DownloadCancelled("Download cancelled by user")

        if d.get("status") != "started":
            return

        name = d.get("postprocessor")
        # MoveFiles is bookkeeping, not work the user needs to see.
        if name == "MoveFiles":
            return

        # yt-dlp fires 'started' twice for some postprocessors (Metadata and
        # ExtractAudio among them), which would log and emit each one twice.
        if name in self._announced_postprocessors:
            return
        self._announced_postprocessors.add(name)

        logger.info(f"Post-processing: {name}")
        if self.on_progress:
            self.on_progress(Progress(
                status="processing",
                postprocessor=name,
                stream_index=len(self._stream_ids) or 1,
                stream_total=max(self._expected_streams(), len(self._stream_ids)),
                info=self._info,
            ))

    def get_info(self, url: str, flat: bool = False) -> VideoInfo:
        """Extract information from a video or playlist without downloading.

        Args:
            url: Video or playlist URL
            flat: Skip per-entry extraction for playlists. A 19-video playlist
                resolves in ~1s instead of ~30s, at the cost of the per-format
                detail -- the right trade for a preview card.
        """
        opts = self.config.get_ydl_opts(self.output_path, self.verbose)
        opts["skip_download"] = True
        if flat:
            opts["extract_flat"] = "in_playlist"
        try:
            with YoutubeDL(opts) as ydl:
                raw = ydl.extract_info(url, download=False)
        except YtDlpDownloadError as e:
            raise DownloadError(f"Error extracting video information: {e}") from e

        if raw is None:
            raise DownloadError(f"No video information returned for: {url}")

        return VideoInfo.from_ydl(raw)

    @staticmethod
    def _log_video_info(info: VideoInfo) -> None:
        """Logs video information"""
        logger.info(f"Title: {info.title}")
        logger.info(f"Channel: {info.uploader}")
        logger.info(f"Duration: {info.duration_display}")
        if info.is_playlist:
            logger.info(f"Playlist with {info.entry_count} videos")

    @staticmethod
    def _resolve_filepath(info: dict[str, Any]) -> Optional[Path]:
        """Find the final file on disk, after any postprocessor renamed it."""
        downloads = info.get("requested_downloads") or []
        for entry in downloads:
            path = entry.get("filepath") or entry.get("_filename")
            if path:
                return Path(path)
        path = info.get("filepath") or info.get("_filename")
        return Path(path) if path else None

    def download(self, url: str) -> DownloadResult:
        """
        Download a video from the provided URL

        Args:
            url: Video URL

        Returns:
            A DownloadResult carrying the metadata, final path, or error.
        """
        logger.info(f"Starting download: {url}")
        self.ensure_output_directory()
        self._cancelled.clear()
        self._stream_ids.clear()
        self._info = None
        self._announced_postprocessors.clear()

        opts = self.config.get_ydl_opts(self.output_path, self.verbose)
        opts["progress_hooks"] = [self._progress_hook]
        opts["postprocessor_hooks"] = [self._postprocessor_hook]

        try:
            # One extract_info(download=True) call: the previous version
            # extracted metadata and then downloaded separately, paying for two
            # full extractions (and two chances for YouTube to rate-limit).
            with YoutubeDL(opts) as ydl:
                raw = ydl.extract_info(url, download=True)

            if raw is None:
                raise DownloadError("yt-dlp returned no information for this URL")

            info = VideoInfo.from_ydl(raw)
            self._log_video_info(info)

            logger.info("✓ Download completed successfully!")
            return DownloadResult(
                success=True,
                info=info,
                filepath=self._resolve_filepath(raw),
            )

        except DownloadCancelled as e:
            logger.warning(f"Download cancelled: {url}")
            return DownloadResult(success=False, error=str(e))
        except (DownloadError, YtDlpDownloadError) as e:
            logger.error(f"Download error: {e}")
            return DownloadResult(success=False, error=str(e))
        except Exception as e:
            logger.exception("Unexpected error during download")
            return DownloadResult(success=False, error=f"{type(e).__name__}: {e}")
