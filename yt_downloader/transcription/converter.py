import logging
import subprocess
from pathlib import Path

from ..exceptions import TranscriptionError

logger = logging.getLogger(__name__)


class AudioConverter:
    """Converts any audio/video file to the WAV layout Whisper expects."""

    def __init__(self, sample_rate: str = "16k"):
        self.sample_rate = sample_rate

    def convert_to_wav(self, input_file: str | Path, output_file: str | Path) -> Path:
        """Convert to 16-bit mono PCM WAV.

        Uses ffmpeg directly rather than the ffmpeg-python wrapper: it is one
        less dependency for a single fixed command, and the error message comes
        back as plain text instead of needing to be decoded from an exception.
        """
        input_file, output_file = Path(input_file), Path(output_file)
        logger.info("Converting to WAV...")

        command = [
            "ffmpeg",
            "-nostdin",
            "-loglevel", "error",
            "-i", str(input_file),
            "-vn",                    # drop any video stream
            "-acodec", "pcm_s16le",
            "-ac", "1",               # mono
            "-ar", self.sample_rate,
            "-y",                     # overwrite
            str(output_file),
        ]

        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
        except FileNotFoundError as e:
            raise TranscriptionError(
                "ffmpeg not found. Install it with `sudo apt install ffmpeg` "
                "(Debian/Ubuntu) or `brew install ffmpeg` (macOS)."
            ) from e

        if result.returncode != 0:
            detail = (result.stderr or "").strip().splitlines()
            raise TranscriptionError(
                f"ffmpeg failed converting {input_file.name}: "
                f"{detail[-1] if detail else 'unknown error'}"
            )

        if not output_file.is_file() or output_file.stat().st_size == 0:
            raise TranscriptionError(f"Conversion produced no audio for {input_file.name}")

        return output_file
