import subprocess
from pathlib import Path

import pytest

from yt_downloader.exceptions import TranscriptionError, WhisperNotInstalled
from yt_downloader.transcription import (
    AudioConverter,
    TranscriptionConfig,
    TranscriptionService,
)
from yt_downloader.transcription.transcriber import (
    Transcriber,
    TranscriptionProgress,
    TranscriptionResult,
)


# --------------------------- config ---------------------------

def test_rejects_unknown_model():
    with pytest.raises(ValueError, match="Unknown Whisper model"):
        TranscriptionConfig(whisper_model="enormous")


def test_turbo_cannot_translate():
    """Whisper's turbo model has no translation task; fail early, not mid-run."""
    with pytest.raises(ValueError, match="cannot translate"):
        TranscriptionConfig(whisper_model="turbo", task="translate")

    # ...but turbo transcribing is fine, and medium may translate.
    TranscriptionConfig(whisper_model="turbo")
    TranscriptionConfig(whisper_model="medium", task="translate")


def test_language_may_be_none_for_autodetect():
    assert TranscriptionConfig(language=None).language is None


# --------------------------- converter ---------------------------

def test_converter_reports_missing_ffmpeg(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(TranscriptionError, match="ffmpeg not found"):
        AudioConverter().convert_to_wav(tmp_path / "in.mp4", tmp_path / "out.wav")


def test_converter_surfaces_ffmpeg_failure(monkeypatch, tmp_path):
    class Failed:
        returncode = 1
        stderr = "some/file.mp4: Invalid data found when processing input"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Failed())
    with pytest.raises(TranscriptionError, match="Invalid data found"):
        AudioConverter().convert_to_wav(tmp_path / "in.mp4", tmp_path / "out.wav")


def test_converter_targets_mono_16bit(monkeypatch, tmp_path):
    """Whisper expects 16 kHz mono PCM; the flags must say so."""
    captured = {}

    class Ok:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        out = Path(cmd[cmd.index("-y") + 1])
        out.write_bytes(b"RIFFfake")
        return Ok()

    monkeypatch.setattr(subprocess, "run", fake_run)
    AudioConverter(sample_rate="16k").convert_to_wav(tmp_path / "in.mp4", tmp_path / "out.wav")

    cmd = captured["cmd"]
    assert cmd[cmd.index("-ac") + 1] == "1"
    assert cmd[cmd.index("-ar") + 1] == "16k"
    assert cmd[cmd.index("-acodec") + 1] == "pcm_s16le"
    assert "-vn" in cmd


# --------------------------- results ---------------------------

def test_srt_rendering_uses_comma_milliseconds():
    result = TranscriptionResult(
        text="ok",
        segments=[
            {"start": 0.0, "end": 2.5, "text": " Hello there "},
            {"start": 2.5, "end": 3661.25, "text": "Later"},
            {"start": 5.0, "end": 6.0, "text": "   "},  # blank: skipped
        ],
    )
    srt = result.as_srt()

    assert "00:00:00,000 --> 00:00:02,500" in srt
    assert "01:01:01,250" in srt
    assert "Hello there" in srt
    assert srt.count("-->") == 2


# --------------------------- progress ---------------------------

def test_progress_dataclass_carries_position():
    p = TranscriptionProgress("transcribing", percent=42.0, seconds_done=21, seconds_total=50)
    assert p.percent == 42.0
    assert p.seconds_done == 21


def test_transcriber_reports_whisper_missing(monkeypatch):
    """Without the extra installed, the error must say how to install it."""
    import builtins

    real_import = builtins.__import__

    def no_whisper(name, *args, **kwargs):
        if name == "whisper":
            raise ImportError("No module named 'whisper'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_whisper)

    assert Transcriber.is_available() is False
    with pytest.raises(WhisperNotInstalled, match=r"\[transcribe\]"):
        Transcriber()._require_whisper()


def test_announces_a_first_time_model_download(monkeypatch, caplog):
    """A bare 461 MB tqdm bar with no explanation looks like a hang."""
    import logging

    fake_whisper = type("W", (), {
        "_MODELS": {"small": "https://example/small.pt"},
        "load_model": staticmethod(lambda *a, **k: object()),
    })()
    monkeypatch.setattr(Transcriber, "_require_whisper", staticmethod(lambda: fake_whisper))
    monkeypatch.setattr(Transcriber, "_detect_device", staticmethod(lambda: "cpu"))

    t = Transcriber(TranscriptionConfig(whisper_model="small"))

    monkeypatch.setattr(Transcriber, "_model_is_cached", staticmethod(lambda w, n: False))
    with caplog.at_level(logging.INFO):
        t._model = None
        t.load_model()
    assert any("one time only" in r.message for r in caplog.records)

    # ...and stays quiet once the weights are on disk.
    caplog.clear()
    monkeypatch.setattr(Transcriber, "_model_is_cached", staticmethod(lambda w, n: True))
    with caplog.at_level(logging.INFO):
        t._model = None
        t.load_model()
    assert not any("one time only" in r.message for r in caplog.records)


def test_model_cache_check_never_blocks_loading(monkeypatch):
    """A broken cache probe must not stop a transcription."""
    broken = type("W", (), {})()  # no _MODELS attribute
    assert Transcriber._model_is_cached(broken, "small") is True


# --------------------------- service ---------------------------

def test_url_detection():
    assert TranscriptionService.is_url("https://youtube.com/watch?v=x")
    assert TranscriptionService.is_url("http://example.com/a.mp3")
    assert not TranscriptionService.is_url("/home/me/video.mp4")
    assert not TranscriptionService.is_url("video.mp4")


def test_missing_local_file_is_reported(tmp_path):
    outcome = TranscriptionService().process(str(tmp_path / "nope.mp4"))
    assert outcome.success is False
    assert "File not found" in outcome.error


def test_directory_is_not_a_valid_source(tmp_path):
    outcome = TranscriptionService().process(str(tmp_path))
    assert outcome.success is False
    assert "not a file" in outcome.error


def test_output_path_defaults_beside_local_file():
    resolved = TranscriptionService._resolve_output_path(
        "/media/aula.mp4", Path("/media/aula.mp4"), None
    )
    assert resolved == Path("/media/aula.txt")


def test_output_path_for_url_uses_the_video_title(tmp_path, monkeypatch):
    """The temp media file is named after the video, so the transcript is too."""
    monkeypatch.chdir(tmp_path)
    resolved = TranscriptionService._resolve_output_path(
        "https://youtube.com/watch?v=x", Path("/tmp/xyz/My Talk.mp3"), None
    )
    assert resolved == tmp_path / "My Talk.txt"


def test_explicit_output_wins():
    resolved = TranscriptionService._resolve_output_path(
        "/media/a.mp4", Path("/media/a.mp4"), "~/notes/out.txt"
    )
    assert resolved.name == "out.txt"
    assert "~" not in str(resolved)


def test_service_downloads_audio_without_re_encoding(monkeypatch, tmp_path):
    """The WAV conversion follows, so an intermediate mp3 encode is waste."""
    captured = {}

    class FakeDownloader:
        def __init__(self, output_path=None, config=None, on_progress=None):
            captured["config"] = config

        def download(self, url):
            from yt_downloader.downloader import DownloadResult

            media = Path(captured["workdir"]) / "Title.webm"
            media.write_bytes(b"audio")
            return DownloadResult(success=True, filepath=media)

    monkeypatch.setattr("yt_downloader.transcription.service.VideoDownloader", FakeDownloader)

    service = TranscriptionService()
    captured["workdir"] = tmp_path
    path = service._download_audio("https://yt/x", tmp_path)

    assert path.name == "Title.webm"
    assert captured["config"].audio_only is True
    assert captured["config"].audio_format == "best"


def test_service_reports_download_failure(monkeypatch, tmp_path):
    class FakeDownloader:
        def __init__(self, **kwargs):
            pass

        def download(self, url):
            from yt_downloader.downloader import DownloadResult

            return DownloadResult(success=False, error="HTTP Error 403: Forbidden")

    monkeypatch.setattr("yt_downloader.transcription.service.VideoDownloader", FakeDownloader)

    with pytest.raises(TranscriptionError, match="403"):
        TranscriptionService()._download_audio("https://yt/x", tmp_path)
