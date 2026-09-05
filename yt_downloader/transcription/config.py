from dataclasses import dataclass
from typing import Optional

# Whisper model sizes, cheapest first. `turbo` is an optimized large-v3: about
# as accurate as `large` but far faster, so it beats `medium` for most uses.
WHISPER_MODELS = ("tiny", "base", "small", "medium", "large", "turbo")

# Models that cannot translate to English, only transcribe in-language.
TRANSCRIBE_ONLY_MODELS = frozenset({"turbo"})


@dataclass
class TranscriptionConfig:
    """Configuration for the transcription process."""

    whisper_model: str = "small"
    # None lets Whisper detect the spoken language itself.
    language: Optional[str] = "pt"
    sample_rate: str = "16k"
    # Whisper is trained on 16 kHz mono audio, so the converter always targets
    # that; sample_rate stays configurable for experiments only.
    task: str = "transcribe"

    def __post_init__(self) -> None:
        if self.whisper_model not in WHISPER_MODELS:
            raise ValueError(
                f"Unknown Whisper model {self.whisper_model!r}. "
                f"Choose one of: {', '.join(WHISPER_MODELS)}"
            )
        if self.task not in ("transcribe", "translate"):
            raise ValueError(f"task must be 'transcribe' or 'translate', got {self.task!r}")
        if self.task == "translate" and self.whisper_model in TRANSCRIBE_ONLY_MODELS:
            raise ValueError(
                f"The {self.whisper_model!r} model cannot translate. "
                "Use 'medium' or 'large' for translation."
            )
