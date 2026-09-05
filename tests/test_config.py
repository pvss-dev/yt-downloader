from pathlib import Path

from yt_downloader.config import DownloaderConfig


def test_format_caps_the_fallback_too():
    """The `best` fallback must repeat the height cap.

    Without it, a video whose adaptive streams are unavailable would fall back
    to an uncapped format and silently ignore the requested quality.
    """
    fmt = DownloaderConfig(max_height=720).build_format()
    assert fmt == "bestvideo[height<=720]+bestaudio/best[height<=720]/best"


def test_format_without_cap():
    assert DownloaderConfig(max_height=None).build_format() == "bestvideo+bestaudio/best"


def test_audio_only_format_ignores_height():
    assert DownloaderConfig(audio_only=True, max_height=360).build_format() == "bestaudio/best"


def test_audio_only_adds_extract_audio_postprocessor():
    opts = DownloaderConfig(audio_only=True, audio_format="flac").get_ydl_opts(Path("/tmp"))
    keys = [p["key"] for p in opts["postprocessors"]]
    assert "FFmpegExtractAudio" in keys
    assert opts["postprocessors"][0]["preferredcodec"] == "flac"
    # An audio file must not be wrapped in a video container.
    assert "merge_output_format" not in opts


def test_single_video_url_does_not_pull_the_playlist():
    """A /watch?v=...&list=... URL must download one video unless asked."""
    assert DownloaderConfig(playlist=False).get_ydl_opts(Path("/tmp"))["noplaylist"] is True
    assert DownloaderConfig(playlist=True).get_ydl_opts(Path("/tmp"))["noplaylist"] is False


def test_playlist_uses_indexed_template():
    opts = DownloaderConfig(playlist=True).get_ydl_opts(Path("/out"))
    assert "playlist_index" in opts["outtmpl"]
    assert opts["outtmpl"].startswith("/out/")


def test_overwrite_flag_maps_to_overwrites():
    assert DownloaderConfig(overwrite_files=True).get_ydl_opts(Path("/tmp"))["overwrites"] is True
    assert DownloaderConfig(overwrite_files=False).get_ydl_opts(Path("/tmp"))["overwrites"] is False


def test_progress_bar_is_suppressed_for_callback_consumers():
    """Callers render progress from the hook, so yt-dlp must not also print."""
    assert DownloaderConfig().get_ydl_opts(Path("/tmp"))["noprogress"] is True


def test_subtitles_only_when_requested():
    plain = DownloaderConfig().get_ydl_opts(Path("/tmp"))
    assert "writesubtitles" not in plain

    subs = DownloaderConfig(write_subtitles=True).get_ydl_opts(Path("/tmp"))
    assert subs["writesubtitles"] is True
    assert subs["subtitleslangs"] == ["pt", "en"]
