import argparse
import logging
import sys
from typing import Optional

from ..exceptions import WhisperNotInstalled
from .config import WHISPER_MODELS, TranscriptionConfig
from .service import TranscriptionService
from .transcriber import Transcriber, TranscriptionProgress

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def parse_arguments(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="yt-transcribe",
        description="Transcribe a local media file or a YouTube URL with Whisper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage examples:
  %(prog)s video.mp4
  %(prog)s video.mp4 transcricao.txt
  %(prog)s "https://youtube.com/watch?v=VIDEO_ID" --model turbo
  %(prog)s podcast.mp3 --language en --srt
  %(prog)s aula.mp4 --detect-language
        """,
    )

    parser.add_argument("source", help="Local media file or URL")
    parser.add_argument(
        "output",
        nargs="?",
        help="Transcript file (default: named after the source, with .txt)",
    )
    parser.add_argument(
        "-m", "--model",
        default="small",
        choices=WHISPER_MODELS,
        help="Whisper model (default: small; 'turbo' is fast and accurate)",
    )
    parser.add_argument(
        "-l", "--language",
        default="pt",
        help="Spoken language code, e.g. pt, en, es (default: pt)",
    )
    parser.add_argument(
        "--detect-language",
        action="store_true",
        help="Let Whisper detect the language instead of assuming one",
    )
    parser.add_argument(
        "--translate",
        action="store_true",
        help="Translate to English instead of transcribing (not supported by 'turbo')",
    )
    parser.add_argument(
        "--srt",
        action="store_true",
        help="Also write a .srt subtitle file next to the transcript",
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Hide the progress bar",
    )

    return parser.parse_args(argv)


def render_progress(progress: TranscriptionProgress) -> None:
    """Single-line progress bar on stderr."""
    if progress.status == "loading_model":
        sys.stderr.write(
            f"  loading {progress.model} on {(progress.device or '?').upper()}...\n"
        )
        return

    if progress.status == "finished":
        sys.stderr.write("\n")
        return

    if progress.percent is None:
        return

    filled = int(progress.percent / 100 * 30)
    bar = "█" * filled + "░" * (30 - filled)
    done = progress.seconds_done or 0
    total = progress.seconds_total or 0
    sys.stderr.write(f"\r  [{bar}] {progress.percent:5.1f}%  {done:.0f}s/{total:.0f}s")
    sys.stderr.flush()


def main(argv: Optional[list[str]] = None) -> int:
    try:
        args = parse_arguments(argv)

        if not Transcriber.is_available():
            logger.error(WhisperNotInstalled.MESSAGE)
            return 1

        try:
            config = TranscriptionConfig(
                whisper_model=args.model,
                language=None if args.detect_language else args.language,
                task="translate" if args.translate else "transcribe",
            )
        except ValueError as e:
            logger.error(str(e))
            return 2

        service = TranscriptionService(
            config,
            on_transcribe_progress=None if args.quiet else render_progress,
        )

        outcome = service.process(args.source, args.output, write_srt=args.srt)

        if not outcome.success:
            logger.error(outcome.error or "Transcription failed")
            return 1

        print(f"Transcript: {outcome.transcript_path}")
        if args.srt:
            print(f"Subtitles:  {outcome.transcript_path.with_suffix('.srt')}")
        if outcome.result and outcome.result.language:
            print(f"Language:   {outcome.result.language}")
        return 0

    except KeyboardInterrupt:
        logger.warning("\nCancelled by user")
        return 130
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
