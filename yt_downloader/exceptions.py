class DownloaderException(Exception):
    """Base exception for downloader errors"""
    pass


class DownloadError(DownloaderException):
    """Error downloading video"""
    pass


class ConfigError(DownloaderException):
    """Downloader configuration error"""
    pass


class DirectoryError(DownloaderException):
    """Error creating or accessing directory"""
    pass


class TranscriptionError(DownloaderException):
    """Error transcribing audio"""
    pass


class WhisperNotInstalled(TranscriptionError):
    """Whisper (and PyTorch) are an optional extra that is not installed."""

    MESSAGE = (
        "Transcription needs Whisper, which is an optional extra.\n"
        "Install it with:  pip install -e \".[transcribe]\"\n"
        "On a machine without an NVIDIA GPU, install the smaller CPU-only "
        "PyTorch first:\n"
        "  pip install torch --index-url https://download.pytorch.org/whl/cpu"
    )

    def __init__(self, message: str | None = None):
        super().__init__(message or self.MESSAGE)
