from dataclasses import dataclass
from pathlib import Path


@dataclass
class DownloaderConfig:
    """Configuration for video downloader"""

    # Directories
    DEFAULT_OUTPUT_DIR: str = "./videos"

    # Quality
    MAX_HEIGHT: int = 1080
    PREFERRED_FORMAT: str = "mkv"

    # Performance
    CONCURRENT_FRAGMENTS: int = 4

    # Behavior
    OVERWRITE_FILES: bool = False
    CONTINUE_DOWNLOADS: bool = True
    ADD_METADATA: bool = True

    # Output
    FILENAME_TEMPLATE: str = "%(title)s.%(ext)s"

    def get_ydl_opts(self, output_path: Path, verbose: bool = False) -> dict:
        """Returns configured options for yt-dlp"""
        return {
            'format': f'bestvideo[height<={self.MAX_HEIGHT}]+bestaudio/best',
            'merge_output_format': self.PREFERRED_FORMAT,
            'quiet': not verbose,
            'no_warnings': not verbose,
            'concurrent_fragment_downloads': self.CONCURRENT_FRAGMENTS,
            'outtmpl': str(output_path / self.FILENAME_TEMPLATE),
            'nooverwrites': not self.OVERWRITE_FILES,
            'continuedl': self.CONTINUE_DOWNLOADS,
            'postprocessors': [
                {
                    'key': 'FFmpegMetadata',
                    'add_metadata': self.ADD_METADATA,
                }
            ] if self.ADD_METADATA else [],
        }
