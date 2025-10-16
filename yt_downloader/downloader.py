import logging
from pathlib import Path
from typing import Optional, Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError as YtDlpDownloadError

from .config import DownloaderConfig
from .exceptions import DownloadError, DirectoryError

logger = logging.getLogger(__name__)


class VideoDownloader:
    """Manage YouTube video downloads"""

    def __init__(
            self,
            output_path: str = DownloaderConfig.DEFAULT_OUTPUT_DIR,
            verbose: bool = False,
            config: Optional[DownloaderConfig] = None
    ):
        """
        Initialize the downloader

        Args:
            output_path: Path to save videos
            verbose: Shows detailed information
            config: Custom configuration (optional)
        """
        self.output_path = Path(output_path)
        self.verbose = verbose
        self.config = config or DownloaderConfig()
        self._setup_output_directory()

    def _setup_output_directory(self) -> None:
        """Creates the output directory if it does not exist"""
        try:
            self.output_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"Output directory: {self.output_path.absolute()}")
        except OSError as e:
            raise DirectoryError(f"Error creating directory {self.output_path}: {e}")

    @staticmethod
    def _progress_hook(d: dict[str, Any]) -> None:
        """Hook to show download progress"""
        if d['status'] == 'downloading':
            percent = d.get('_percent_str', 'N/A')
            speed = d.get('_speed_str', 'N/A')
            eta = d.get('_eta_str', 'N/A')
            logger.info(f"Progress: {percent} | Speed: {speed} | ETA: {eta}")
        elif d['status'] == 'finished':
            logger.info("Download completed, processing file...")

    def _get_video_info(self, url: str) -> dict[str, Any]:
        """Extract information from video without downloading"""
        try:
            opts = self.config.get_ydl_opts(self.output_path, self.verbose)
            with YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        except YtDlpDownloadError as e:
            raise DownloadError(f"Error extracting video information: {e}")

    @staticmethod
    def _log_video_info(info: dict[str, Any]) -> None:
        """Logs video information"""
        title = info.get('title', 'Unknown')
        duration = info.get('duration', 0)
        uploader = info.get('uploader', 'Unknown')

        logger.info(f"Title: {title}")
        logger.info(f"Channel: {uploader}")
        logger.info(f"Duration: {duration // 60}:{duration % 60:02d}")

    def download(self, url: str) -> bool:
        """
        Download a video from the provided URL

        Args:
            url: Video URL

        Returns:
            True if the download was successful, False otherwise
        """
        logger.info(f"Starting download: {url}")

        try:
            # Extract information
            info = self._get_video_info(url)
            self._log_video_info(info)

            # Set options with progress hook
            opts = self.config.get_ydl_opts(self.output_path, self.verbose)
            if self.verbose:
                opts['progress_hooks'] = [self._progress_hook]

            # Download
            with YoutubeDL(opts) as ydl:
                ydl.download([url])

            logger.info("✓ Download completed successfully!")
            return True

        except DownloadError as e:
            logger.error(f"Download error: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return False
