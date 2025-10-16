import argparse
import logging
import sys
from typing import Optional

from .downloader import VideoDownloader
from .config import DownloaderConfig

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description='Download YouTube videos in high quality',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage examples:
  %(prog)s https://youtube.com/watch?v=VIDEO_ID
  %(prog)s https://youtube.com/watch?v=VIDEO_ID -o ~/Downloads
  %(prog)s https://youtube.com/watch?v=VIDEO_ID -o ./videos -v
  %(prog)s https://youtube.com/watch?v=VIDEO_ID --max-quality 720
        """
    )

    parser.add_argument(
        'url',
        help='YouTube Video URL'
    )

    parser.add_argument(
        '-o', '--output',
        default=DownloaderConfig.DEFAULT_OUTPUT_DIR,
        help=f'Destination folder (default: {DownloaderConfig.DEFAULT_OUTPUT_DIR})'
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Show detailed information during download'
    )

    parser.add_argument(
        '--max-quality',
        type=int,
        choices=[360, 480, 720, 1080, 1440, 2160],
        help='Maximum video quality (height in pixels)'
    )

    parser.add_argument(
        '--overwrite',
        action='store_true',
        help='Overwrite existing files'
    )

    return parser.parse_args()


def main() -> int:
    """Main CLI Function"""
    try:
        args = parse_arguments()

        # Configure custom options if needed
        config = None
        if args.max_quality or args.overwrite:
            config = DownloaderConfig()
            if args.max_quality:
                config.MAX_HEIGHT = args.max_quality
            if args.overwrite:
                config.OVERWRITE_FILES = True

        # Create downloader and run
        downloader = VideoDownloader(
            output_path=args.output,
            verbose=args.verbose,
            config=config
        )

        success = downloader.download(args.url)
        return 0 if success else 1

    except KeyboardInterrupt:
        logger.warning("\nDownload canceled by user")
        return 130
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1
