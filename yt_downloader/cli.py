import argparse
import logging
import sys

from .config import DownloaderConfig
from .downloader import Progress, VideoDownloader
from .transcription.config import WHISPER_MODELS

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

    transcription = parser.add_argument_group('transcription (needs the [transcribe] extra)')

    transcription.add_argument(
        '--transcribe',
        action='store_true',
        help='Transcribe the audio after downloading, writing a .txt alongside it'
    )

    transcription.add_argument(
        '--whisper-model',
        default='small',
        choices=WHISPER_MODELS,
        help='Whisper model for --transcribe (default: small)'
    )

    transcription.add_argument(
        '--language',
        default='pt',
        help='Spoken language for --transcribe, e.g. pt, en (default: pt)'
    )

    transcription.add_argument(
        '--detect-language',
        action='store_true',
        help='Let Whisper detect the language instead of assuming one'
    )

    transcription.add_argument(
        '--srt',
        action='store_true',
        help='With --transcribe, also write a .srt subtitle file'
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


def _transcribe_downloaded(args: argparse.Namespace, result) -> int:
    """Transcribe the file that was just downloaded.

    Runs against the local file rather than re-fetching the audio, so
    `--transcribe` costs one download, not two.
    """
    # Imported here so the plain download path never pays for loading the
    # transcription stack.
    from .exceptions import WhisperNotInstalled
    from .transcription import TranscriptionConfig, TranscriptionService
    from .transcription.cli import render_progress
    from .transcription.transcriber import Transcriber

    if not Transcriber.is_available():
        logger.error(WhisperNotInstalled.MESSAGE)
        return 1

    if not result.filepath:
        logger.error('Cannot transcribe: the downloaded file path is unknown')
        return 1

    try:
        config = TranscriptionConfig(
            whisper_model=args.whisper_model,
            language=None if args.detect_language else args.language,
        )
    except ValueError as e:
        logger.error(str(e))
        return 2

    service = TranscriptionService(
        config,
        on_transcribe_progress=None if args.verbose else render_progress,
    )
    outcome = service.process(str(result.filepath), write_srt=args.srt)

    if not outcome.success:
        logger.error(outcome.error or 'Transcription failed')
        return 1

    print(f'Transcript: {outcome.transcript_path}')
    if args.srt:
        print(f"Subtitles:  {outcome.transcript_path.with_suffix('.srt')}")
    return 0


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

        if not result.success:
            logger.error(result.error or 'Download failed')
            return 1

        if result.filepath:
            print(f'Saved to: {result.filepath}')

        if args.transcribe:
            return _transcribe_downloaded(args, result)

        return 0

    except KeyboardInterrupt:
        if downloader:
            downloader.cancel()
        logger.warning("\nDownload canceled by user")
        return 130
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1
