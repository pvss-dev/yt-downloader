import logging
import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ..exceptions import TranscriptionError, WhisperNotInstalled
from .config import TranscriptionConfig

logger = logging.getLogger(__name__)

TranscribeProgress = Callable[["TranscriptionProgress"], None]


@dataclass
class TranscriptionProgress:
    """How far Whisper has gotten through the audio."""

    status: str                       # loading_model | transcribing | finished
    percent: Optional[float] = None
    seconds_done: Optional[float] = None
    seconds_total: Optional[float] = None
    device: Optional[str] = None
    model: Optional[str] = None


@dataclass
class TranscriptionResult:
    """A finished transcript plus the metadata worth keeping."""

    text: str
    language: Optional[str] = None
    duration: Optional[float] = None
    segments: list[dict[str, Any]] = field(default_factory=list)

    def as_plain_text(self) -> str:
        return self.text

    def as_srt(self) -> str:
        """Render the segments as an SRT subtitle file."""
        def stamp(seconds: float) -> str:
            ms = int(round(seconds * 1000))
            hours, ms = divmod(ms, 3_600_000)
            minutes, ms = divmod(ms, 60_000)
            secs, ms = divmod(ms, 1000)
            return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"

        blocks = []
        for index, segment in enumerate(self.segments, start=1):
            text = (segment.get("text") or "").strip()
            if not text:
                continue
            blocks.append(
                f"{index}\n{stamp(segment['start'])} --> {stamp(segment['end'])}\n{text}\n"
            )
        return "\n".join(blocks)


class Transcriber:
    """Transcribes audio with Whisper, reporting progress as it goes."""

    def __init__(
            self,
            config: Optional[TranscriptionConfig] = None,
            on_progress: Optional[TranscribeProgress] = None,
    ):
        self.config = config or TranscriptionConfig()
        self.on_progress = on_progress
        self._model = None
        self._device: Optional[str] = None

    # ------------------------------------------------------------------
    # environment
    # ------------------------------------------------------------------

    @staticmethod
    def is_available() -> bool:
        """Whether the optional Whisper extra is installed."""
        try:
            import whisper  # noqa: F401
        except ImportError:
            return False
        return True

    @staticmethod
    def _require_whisper():
        try:
            import whisper
        except ImportError as e:
            raise WhisperNotInstalled() from e
        return whisper

    @staticmethod
    def _model_is_cached(whisper, name: str) -> bool:
        """Whether the weights are already on disk, so no download follows."""
        try:
            url = whisper._MODELS[name]
            default = os.path.join(os.path.expanduser("~"), ".cache")
            root = os.path.join(os.getenv("XDG_CACHE_HOME", default), "whisper")
            return os.path.isfile(os.path.join(root, os.path.basename(url)))
        except Exception:
            # Only drives a log line; never block loading over it.
            return True

    @property
    def device(self) -> str:
        if self._device is None:
            self._device = self._detect_device()
        return self._device

    @staticmethod
    def _detect_device() -> str:
        """Pick CUDA, then Apple MPS, then CPU."""
        try:
            import torch
        except ImportError:
            logger.warning("PyTorch not found. Using CPU")
            return "cpu"

        try:
            if torch.cuda.is_available():
                logger.info(f"Using GPU: {torch.cuda.get_device_name(0)}")
                return "cuda"
            if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                logger.info("Using Apple Silicon GPU (MPS)")
                return "mps"
        except Exception as e:
            logger.warning(f"Error detecting device: {e}. Using CPU")
            return "cpu"

        logger.info("GPU not available. Using CPU (this will be slower)")
        return "cpu"

    # ------------------------------------------------------------------
    # model
    # ------------------------------------------------------------------

    def load_model(self):
        """Load the Whisper model, falling back to CPU if the GPU refuses."""
        if self._model is not None:
            return self._model

        whisper = self._require_whisper()
        self._emit(TranscriptionProgress(
            "loading_model", device=self.device, model=self.config.whisper_model,
        ))

        if not self._model_is_cached(whisper, self.config.whisper_model):
            # Whisper prints a bare tqdm bar while fetching the weights; say
            # what it is so a sudden 461 MB download isn't a mystery.
            logger.info(
                f"Downloading the Whisper '{self.config.whisper_model}' model "
                f"(one time only, cached in ~/.cache/whisper)..."
            )

        logger.info(
            f"Loading Whisper model ({self.config.whisper_model}) on {self.device.upper()}..."
        )

        try:
            self._model = whisper.load_model(self.config.whisper_model, device=self.device)
        except Exception as e:
            if self.device == "cpu":
                raise TranscriptionError(f"Failed to load Whisper model: {e}") from e
            logger.warning(f"Failed to load on {self.device}, falling back to CPU: {e}")
            self._device = "cpu"
            self._model = whisper.load_model(self.config.whisper_model, device="cpu")

        logger.info("Model loaded successfully")
        return self._model

    # ------------------------------------------------------------------
    # transcription
    # ------------------------------------------------------------------

    def _emit(self, progress: TranscriptionProgress) -> None:
        if self.on_progress:
            self.on_progress(progress)

    def transcribe(self, wav_file: str | Path) -> TranscriptionResult:
        """Transcribe a WAV file prepared by AudioConverter."""
        wav_file = Path(wav_file)
        if not wav_file.is_file():
            raise TranscriptionError(f"WAV file not found: {wav_file}")

        model = self.load_model()
        logger.info(f"Transcribing audio using {self.device.upper()}...")

        options: dict[str, Any] = {
            "task": self.config.task,
            "verbose": None,  # keeps Whisper's own stdout quiet
        }
        if self.config.language:
            options["language"] = self.config.language

        with _ProgressTap(wav_file, self._emit), warnings.catch_warnings():
            # Whisper warns that CPU has no FP16 on every single run. We already
            # log which device is in use, so it adds nothing but noise.
            warnings.filterwarnings(
                "ignore", message="FP16 is not supported on CPU", category=UserWarning
            )
            try:
                raw = model.transcribe(str(wav_file), **options)
            except Exception as e:
                raise TranscriptionError(f"Transcription failed: {e}") from e

        segments = raw.get("segments") or []
        # One line per segment reads far better than Whisper's single blob.
        text = "\n".join(s["text"].strip() for s in segments if s.get("text", "").strip())

        self._emit(TranscriptionProgress("finished", percent=100.0))
        logger.info("Transcription completed!")

        return TranscriptionResult(
            text=text or (raw.get("text") or "").strip(),
            language=raw.get("language"),
            duration=segments[-1]["end"] if segments else None,
            segments=segments,
        )


class _ProgressTap:
    """Turns Whisper's internal tqdm bar into progress callbacks.

    openai-whisper exposes no progress hook: it drives a tqdm bar over audio
    frames inside `transcribe()`. Swapping that tqdm for a shim is the only way
    to report progress without forking the library, so this context manager
    patches it for the duration of the call and always restores it.
    """

    def __init__(self, wav_file: Path, emit: Callable[[TranscriptionProgress], None]):
        self.wav_file = wav_file
        self.emit = emit
        self.module = None
        self.original = None

    # Whisper's bar counts mel frames; SAMPLE_RATE / HOP_LENGTH = 100 per second.
    FRAMES_PER_SECOND = 100

    def __enter__(self):
        try:
            # `import whisper.transcribe` binds the *function* of that name that
            # whisper/__init__.py re-exports, not the module. importlib is what
            # actually hands back the module object.
            import importlib

            module = importlib.import_module("whisper.transcribe")
        except ImportError:
            return self
        if not hasattr(module, "tqdm"):
            return self

        self.module = module
        self.original = module.tqdm
        emit = self.emit
        frames_per_second = self.FRAMES_PER_SECOND

        class _Shim(self.original.tqdm):
            """Counts its own progress.

            Whisper builds the bar with `disable=verbose is not False`, and we
            pass verbose=None to keep it quiet -- so tqdm's own `update()`
            short-circuits and never advances `self.n`. Tracking the count here
            keeps progress working while the bar stays silent.
            """

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._seen = 0

            def update(self, n=1):
                super().update(n)
                self._seen += n or 0
                if not self.total:
                    return
                emit(TranscriptionProgress(
                    "transcribing",
                    percent=min(100.0, self._seen / self.total * 100),
                    seconds_done=self._seen / frames_per_second,
                    seconds_total=self.total / frames_per_second,
                ))

        # whisper calls `tqdm.tqdm(...)`, so the replacement must expose the
        # same attribute path rather than being the class itself.
        class _Namespace:
            tqdm = _Shim

        module.tqdm = _Namespace
        return self

    def __exit__(self, *exc):
        if self.module is not None and self.original is not None:
            self.module.tqdm = self.original
        return False
