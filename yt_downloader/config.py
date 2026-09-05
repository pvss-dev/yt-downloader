from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class DownloaderConfig:
    """Configuration for video downloader"""

    # Directories
    default_output_dir: str = "./videos"

    # Quality
    max_height: Optional[int] = 1080
    preferred_format: str = "mkv"

    # Audio-only extraction (ignores max_height / preferred_format)
    audio_only: bool = False
    audio_format: str = "mp3"
    audio_quality: str = "192"

    # Performance
    concurrent_fragments: int = 4
    retries: int = 10

    # Behavior
    overwrite_files: bool = False
    continue_downloads: bool = True
    add_metadata: bool = True
    embed_thumbnail: bool = False
    write_subtitles: bool = False
    subtitle_languages: list[str] = field(default_factory=lambda: ["pt", "en"])
    playlist: bool = False

    # Output
    filename_template: str = "%(title)s.%(ext)s"
    playlist_template: str = "%(playlist_title)s/%(playlist_index)02d - %(title)s.%(ext)s"

    def build_format(self) -> str:
        """Build the yt-dlp format selector.

        The `best` fallback repeats the height cap; without it a video whose
        adaptive streams are unavailable would silently download at full
        resolution, ignoring the limit the caller asked for.
        """
        if self.audio_only:
            return "bestaudio/best"
        if not self.max_height:
            return "bestvideo+bestaudio/best"
        cap = f"[height<={self.max_height}]"
        return f"bestvideo{cap}+bestaudio/best{cap}/best"

    def _build_postprocessors(self) -> list[dict]:
        postprocessors: list[dict] = []

        if self.audio_only:
            postprocessors.append({
                "key": "FFmpegExtractAudio",
                "preferredcodec": self.audio_format,
                "preferredquality": self.audio_quality,
            })

        if self.add_metadata:
            postprocessors.append({
                "key": "FFmpegMetadata",
                "add_metadata": True,
            })

        if self.embed_thumbnail:
            postprocessors.append({"key": "EmbedThumbnail"})

        if self.write_subtitles and not self.audio_only:
            postprocessors.append({
                "key": "FFmpegEmbedSubtitle",
                "already_have_subtitle": False,
            })

        return postprocessors

    def get_ydl_opts(self, output_path: Path, verbose: bool = False) -> dict:
        """Returns configured options for yt-dlp"""
        template = self.playlist_template if self.playlist else self.filename_template

        opts = {
            "format": self.build_format(),
            "quiet": not verbose,
            "no_warnings": not verbose,
            # `quiet` alone still lets the progress bar through; the library
            # callers render their own progress from the hook.
            "noprogress": True,
            "concurrent_fragment_downloads": self.concurrent_fragments,
            "retries": self.retries,
            "fragment_retries": self.retries,
            "outtmpl": str(output_path / template),
            "overwrites": self.overwrite_files,
            "continuedl": self.continue_downloads,
            "noplaylist": not self.playlist,
            "ignoreerrors": self.playlist,
            "postprocessors": self._build_postprocessors(),
        }

        if not self.audio_only:
            opts["merge_output_format"] = self.preferred_format

        if self.embed_thumbnail:
            opts["writethumbnail"] = True

        if self.write_subtitles and not self.audio_only:
            opts["writesubtitles"] = True
            opts["subtitleslangs"] = self.subtitle_languages

        return opts
