import argparse
import logging
import sys

from .config import DownloaderConfig
from .downloader import Progress, VideoDownloader

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

QUALITY_CHOICES = [360, 480, 720, 1080, 1440, 2160]


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        prog='yt-download',
        description='Download YouTube videos in high quality',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage examples:
  %(prog)s https://youtube.com/watch?v=VIDEO_ID
  %(prog)s https://youtube.com/watch?v=VIDEO_ID -o ~/Downloads
  %(prog)s https://youtube.com/watch?v=VIDEO_ID --max-quality 720
  %(prog)s https://youtube.com/watch?v=VIDEO_ID --audio-only
  %(prog)s https://youtube.com/playlist?list=ID --playlist
  %(prog)s https://youtube.com/watch?v=VIDEO_ID --info
        """
    )

    parser.add_argument('url', help='YouTube Video URL')

    parser.add_argument(
        '-o', '--output',
        default=DownloaderConfig.default_output_dir,
        help=f'Destination folder (default: {DownloaderConfig.default_output_dir})'
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Show detailed information during download'
    )

    parser.add_argument(
        '--max-quality',
        type=int,
        choices=QUALITY_CHOICES,
        help='Maximum video quality (height in pixels)'
    )

    parser.add_argument(
        '--overwrite',
        action='store_true',
        help='Overwrite existing files'
    )

    parser.add_argument(
        '--audio-only',
        action='store_true',
        help='Extract audio only (mp3 by default)'
    )

    parser.add_argument(
        '--audio-format',
        default='mp3',
        choices=['mp3', 'm4a', 'opus', 'flac', 'wav'],
        help='Audio codec when using --audio-only (default: mp3)'
    )

    parser.add_argument(
        '--playlist',
        action='store_true',
        help='Download the whole playlist instead of just the single video'
    )

    parser.add_argument(
        '--subtitles',
        action='store_true',
        help='Download and embed subtitles'
    )

    parser.add_argument(
        '--thumbnail',
        action='store_true',
        help='Embed the video thumbnail as cover art'
    )

    parser.add_argument(
        '--info',
        action='store_true',
        help='Only print video information, without downloading'
    )

    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> DownloaderConfig:
    """Translate CLI flags into a DownloaderConfig"""
    return DownloaderConfig(
        max_height=args.max_quality or DownloaderConfig.max_height,
        overwrite_files=args.overwrite,
        audio_only=args.audio_only,
        audio_format=args.audio_format,
        playlist=args.playlist,
        write_subtitles=args.subtitles,
        embed_thumbnail=args.thumbnail,
    )


def _render_progress(progress: Progress) -> None:
    """Draw a single-line progress bar on stderr"""
    if progress.status != 'downloading':
        if progress.status == 'finished':
            sys.stderr.write('\n')
        return

    percent = progress.percent
    if percent is None:
        sys.stderr.write(f'\r  downloading... {progress.downloaded_bytes / 1e6:.1f} MB')
        sys.stderr.flush()
        return

    filled = int(percent / 100 * 30)
    bar = '█' * filled + '░' * (30 - filled)
    speed = f'{progress.speed / 1e6:.1f} MB/s' if progress.speed else '--'
    eta = f'{progress.eta}s' if progress.eta is not None else '--'
    sys.stderr.write(f'\r  [{bar}] {percent:5.1f}%  {speed:>10}  ETA {eta:>5}')
    sys.stderr.flush()


def main(argv: list[str] | None = None) -> int:
    """Main CLI Function"""
    downloader = None
    try:
        args = parse_arguments(argv)
        config = build_config(args)

        downloader = VideoDownloader(
            output_path=args.output,
            verbose=args.verbose,
            config=config,
            on_progress=None if args.verbose else _render_progress,
        )

        if args.info:
            info = downloader.get_info(args.url)
            print(f'Title:    {info.title}')
            print(f'Channel:  {info.uploader}')
            print(f'Duration: {info.duration_display}')
            print(f'URL:      {info.webpage_url}')
            if info.available_heights:
                heights = ', '.join(f'{h}p' for h in info.available_heights)
                print(f'Quality:  {heights}')
            if info.is_playlist:
                print(f'Playlist: {info.entry_count} videos')
            return 0

        result = downloader.download(args.url)

        if result.success:
            if result.filepath:
                print(f'Saved to: {result.filepath}')
            return 0

        logger.error(result.error or 'Download failed')
        return 1

    except KeyboardInterrupt:
        if downloader:
            downloader.cancel()
        logger.warning("\nDownload canceled by user")
        return 130
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1
