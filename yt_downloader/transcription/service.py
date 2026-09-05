import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..config import DownloaderConfig
from ..downloader import ProgressCallback, VideoDownloader
from ..exceptions import TranscriptionError
from .config import TranscriptionConfig
from .converter import AudioConverter
from .transcriber import TranscribeProgress, Transcriber, TranscriptionResult

logger = logging.getLogger(__name__)


@dataclass
class TranscriptionOutcome:
    """What a full source -> transcript run produced."""

    success: bool
    result: Optional[TranscriptionResult] = None
    transcript_path: Optional[Path] = None
    media_path: Optional[Path] = None
    error: Optional[str] = None


class TranscriptionService:
    """Turns a local file or a URL into a transcript.

    Downloads go through the project's own VideoDownloader in audio-only mode
    rather than a second, separate yt-dlp wrapper -- one place to keep current
    when YouTube changes.
    """

    def __init__(
            self,
            config: Optional[TranscriptionConfig] = None,
            on_download_progress: Optional[ProgressCallback] = None,
            on_transcribe_progress: Optional[TranscribeProgress] = None,
    ):
        self.config = config or TranscriptionConfig()
        self.on_download_progress = on_download_progress
        self.converter = AudioConverter(self.config.sample_rate)
        self.transcriber = Transcriber(self.config, on_progress=on_transcribe_progress)

    @staticmethod
    def is_url(path: str) -> bool:
        return path.startswith(("http://", "https://"))

    def _download_audio(self, url: str, workdir: Path) -> Path:
        """Fetch the audio track of a URL into workdir."""
        downloader = VideoDownloader(
            output_path=str(workdir),
            config=DownloaderConfig(
                audio_only=True,
                # Keep the source codec: the WAV conversion happens next
                # anyway, so re-encoding to mp3 first would only lose quality
                # and cost time.
                audio_format="best",
                add_metadata=False,
            ),
            on_progress=self.on_download_progress,
        )

        result = downloader.download(url)
        if not result.success:
            raise TranscriptionError(result.error or "Audio download failed")

        if result.filepath and Path(result.filepath).is_file():
            return Path(result.filepath)

        # Postprocessors can rename the file; fall back to whatever landed.
        candidates = [p for p in workdir.iterdir() if p.is_file()]
        if not candidates:
            raise TranscriptionError("Download reported success but produced no file")
        return max(candidates, key=lambda p: p.stat().st_size)

    def _resolve_source(self, path_or_url: str, workdir: Path) -> Path:
        if self.is_url(path_or_url):
            return self._download_audio(path_or_url, workdir)

        source = Path(path_or_url).expanduser()
        if not source.exists():
            raise TranscriptionError(f"File not found: {source}")
        if not source.is_file():
            raise TranscriptionError(f"Path is not a file: {source}")
        return source

    def process(
            self,
            path_or_url: str,
            output_file: Optional[str] = None,
            write_srt: bool = False,
    ) -> TranscriptionOutcome:
        """Download (if needed), convert, transcribe and save.

        Args:
            path_or_url: Local media file, or a URL to fetch audio from
            output_file: Where to write the transcript (default: alongside the
                source for local files, ./<title>.txt for URLs)
            write_srt: Also write a .srt next to the transcript
        """
        try:
            with tempfile.TemporaryDirectory(prefix="yt-transcribe-") as tmp:
                workdir = Path(tmp)
                source = self._resolve_source(path_or_url, workdir)

                wav = self.converter.convert_to_wav(source, workdir / "audio.wav")
                result = self.transcriber.transcribe(wav)

                target = self._resolve_output_path(path_or_url, source, output_file)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(result.text, encoding="utf-8")
                logger.info(f"Transcript saved at: {target}")

                if write_srt:
                    srt = target.with_suffix(".srt")
                    srt.write_text(result.as_srt(), encoding="utf-8")
                    logger.info(f"Subtitles saved at: {srt}")

                return TranscriptionOutcome(
                    success=True,
                    result=result,
                    transcript_path=target,
                    media_path=source if not self.is_url(path_or_url) else None,
                )

        except TranscriptionError as e:
            logger.error(f"Transcription failed: {e}")
            return TranscriptionOutcome(success=False, error=str(e))
        except Exception as e:
            logger.exception("Unexpected error during transcription")
            return TranscriptionOutcome(success=False, error=f"{type(e).__name__}: {e}")

    @staticmethod
    def _resolve_output_path(path_or_url: str, source: Path, output_file: Optional[str]) -> Path:
        if output_file:
            return Path(output_file).expanduser()
        if TranscriptionService.is_url(path_or_url):
            # `source` lives in a temp dir about to vanish, but its stem is the
            # video title -- a far better default name than "transcription".
            return Path.cwd() / f"{source.stem}.txt"
        return source.with_suffix(".txt")
