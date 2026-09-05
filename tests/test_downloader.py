import pytest

from yt_downloader.config import DownloaderConfig
from yt_downloader.downloader import (
    DownloadCancelled,
    Progress,
    VideoDownloader,
    VideoInfo,
)


# --------------------------- VideoInfo parsing ---------------------------

def test_parses_single_video():
    info = VideoInfo.from_ydl({
        "id": "abc123",
        "title": "Some Video",
        "uploader": "Some Channel",
        "duration": 3725,
        "thumbnail": "http://img/t.jpg",
        "webpage_url": "http://yt/abc123",
        "formats": [{"height": 720}, {"height": 1080}, {"height": 720}, {"vcodec": "none"}],
    })

    assert info.title == "Some Video"
    assert info.is_playlist is False
    assert info.entry_count == 1
    assert info.duration_display == "1:02:05"
    assert info.available_heights == [720, 1080]


def test_missing_duration_does_not_crash():
    """`duration` is absent for live streams; the old code did `duration // 60`."""
    assert VideoInfo.from_ydl({"id": "x", "title": "Live", "duration": None}).duration_display == "--:--"


def test_parses_playlist_and_counts_real_entries():
    info = VideoInfo.from_ydl({
        "id": "PL1",
        "title": "My Playlist",
        "uploader": "Chan",
        "entries": [
            {"id": "a", "title": "One", "duration": 65, "thumbnail": "http://i/a.jpg"},
            None,  # yt-dlp yields None for unavailable entries
            {"id": "b", "title": "Two", "duration": 30},
        ],
    })

    assert info.is_playlist is True
    assert info.entry_count == 2
    assert info.thumbnail == "http://i/a.jpg"


def test_flat_playlist_falls_back_to_thumbnails_list():
    info = VideoInfo.from_ydl({
        "id": "PL2",
        "title": "Flat",
        "entries": [{"id": "a", "thumbnails": [{"url": "small.jpg"}, {"url": "large.jpg"}]}],
    })
    assert info.thumbnail == "large.jpg"


def test_generator_entries_are_materialized_once():
    """`entries` can be a lazy generator; consuming it twice would empty it."""
    info = VideoInfo.from_ydl({
        "id": "PL3",
        "title": "Lazy",
        "entries": (e for e in [{"id": "a", "duration": 10}, {"id": "b", "duration": 20}]),
    })
    assert info.entry_count == 2
    assert info.duration == 10


# --------------------------- progress math ---------------------------

def test_overall_percent_splits_streams_evenly():
    """Video and audio are separate streams; the bar must not restart at 0."""
    video_half = Progress("downloading", downloaded_bytes=50, total_bytes=100,
                          stream_index=1, stream_total=2)
    assert video_half.percent == 50.0
    assert video_half.overall_percent == 25.0

    audio_half = Progress("downloading", downloaded_bytes=50, total_bytes=100,
                          stream_index=2, stream_total=2)
    assert audio_half.percent == 50.0
    assert audio_half.overall_percent == 75.0


def test_overall_percent_never_exceeds_100():
    p = Progress("downloading", downloaded_bytes=200, total_bytes=100,
                 stream_index=2, stream_total=2)
    assert p.overall_percent == 100.0


def test_finished_stream_completes_its_slice():
    assert Progress("finished", stream_index=1, stream_total=2).overall_percent == 50.0
    assert Progress("finished", stream_index=2, stream_total=2).overall_percent == 100.0


def test_processing_reports_full_bar():
    assert Progress("processing", stream_index=2, stream_total=2).overall_percent == 100.0


def test_unknown_total_yields_no_percent():
    assert Progress("downloading", downloaded_bytes=10, total_bytes=None).percent is None
    assert Progress("downloading", downloaded_bytes=10, total_bytes=None).overall_percent is None


# --------------------------- downloader behavior ---------------------------

def test_expected_streams_from_format_selector():
    merged = VideoDownloader(config=DownloaderConfig(max_height=720))
    assert merged._expected_streams() == 2

    audio = VideoDownloader(config=DownloaderConfig(audio_only=True))
    assert audio._expected_streams() == 1


def test_streams_are_tracked_by_format_id():
    d = VideoDownloader()
    assert d._stream_position({"info_dict": {"format_id": "137"}}) == 1
    assert d._stream_position({"info_dict": {"format_id": "137"}}) == 1
    assert d._stream_position({"info_dict": {"format_id": "251"}}) == 2


def test_cancel_raises_inside_the_hook():
    d = VideoDownloader()
    d.cancel()
    with pytest.raises(DownloadCancelled):
        d._progress_hook({"status": "downloading"})


def test_progress_callback_receives_normalized_snapshot():
    seen = []
    d = VideoDownloader(on_progress=seen.append)
    d._progress_hook({
        "status": "downloading",
        "downloaded_bytes": 512,
        "total_bytes_estimate": 1024,  # only an estimate is available
        "speed": 2048.0,
        "eta": 7,
        "info_dict": {"format_id": "137", "title": "T", "uploader": "U"},
    })

    assert len(seen) == 1
    assert seen[0].total_bytes == 1024
    assert seen[0].percent == 50.0
    assert seen[0].info.title == "T"


def test_metadata_comes_from_the_hook_without_a_second_extraction():
    d = VideoDownloader()
    d._progress_hook({
        "status": "downloading",
        "info_dict": {"format_id": "137", "title": "Cached", "uploader": "Chan", "duration": 90},
    })
    assert d._info is not None
    assert d._info.title == "Cached"
    assert d._info.duration_display == "1:30"


def test_movefiles_postprocessor_is_not_surfaced():
    """MoveFiles is bookkeeping; showing it as 'processing' just flickers."""
    seen = []
    d = VideoDownloader(on_progress=seen.append)
    d._postprocessor_hook({"status": "started", "postprocessor": "MoveFiles"})
    assert seen == []

    d._postprocessor_hook({"status": "started", "postprocessor": "Merger"})
    assert [p.status for p in seen] == ["processing"]


def test_output_directory_is_not_created_until_download():
    """get_info must not leave an empty folder behind."""
    d = VideoDownloader(output_path="/tmp/yt-downloader-should-not-exist-xyz")
    assert not d.output_path.exists()
