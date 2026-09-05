import json
import threading

import pytest
from fastapi.testclient import TestClient

from yt_downloader.downloader import DownloadResult, Progress, VideoInfo
from yt_downloader.web import server


@pytest.fixture
def client():
    server.jobs.clear_finished()
    return TestClient(server.app)


class FakeDownloader:
    """Stands in for VideoDownloader so tests never touch the network."""

    info = VideoInfo(
        id="abc", title="Fake Video", uploader="Fake Chan", duration=100,
        thumbnail="http://i/t.jpg", webpage_url="http://yt/abc",
    )

    # Held closed until the test has subscribed, so the SSE stream is exercised
    # against a running job rather than a finished one.
    release = threading.Event()

    def __init__(self, *args, on_progress=None, **kwargs):
        self.on_progress = on_progress

    def get_info(self, url, flat=False):
        return self.info

    def cancel(self):
        pass

    def download(self, url):
        self.release.wait(timeout=5)
        self.on_progress(Progress(
            "downloading", downloaded_bytes=50, total_bytes=100,
            stream_index=1, stream_total=2, info=self.info,
        ))
        self.on_progress(Progress("processing", info=self.info))
        return DownloadResult(success=True, info=self.info, filepath="/tmp/fake.mkv")


def test_health_reports_yt_dlp_version(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["yt_dlp_version"]


def test_index_and_assets_are_served(client):
    assert client.get("/").status_code == 200
    assert "text/css" in client.get("/style.css").headers["content-type"]
    assert client.get("/app.js").status_code == 200


def test_info_endpoint(client, monkeypatch):
    monkeypatch.setattr(server, "VideoDownloader", FakeDownloader)
    body = client.post("/api/info", json={"url": "http://yt/abc"}).json()
    assert body["title"] == "Fake Video"
    assert body["is_playlist"] is False


def test_info_rejects_empty_url(client):
    assert client.post("/api/info", json={"url": ""}).status_code == 422


def test_download_streams_progress_to_completion(client, monkeypatch):
    monkeypatch.setattr("yt_downloader.web.jobs.VideoDownloader", FakeDownloader)

    FakeDownloader.release.clear()
    job = client.post("/api/download", json={"url": "http://yt/abc"}).json()

    events = []
    with client.stream("GET", f"/api/jobs/{job['id']}/events") as response:
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            events.append(json.loads(line[5:].strip()))
            FakeDownloader.release.set()  # let the download proceed
            if events[-1]["status"] in ("completed", "error", "cancelled"):
                break

    # The stream must show real progression, not just a final snapshot.
    # A late subscriber gets the current state replayed, so the first status
    # can repeat; compare the distinct transitions instead.
    transitions = [s for i, s in enumerate([e["status"] for e in events])
                   if i == 0 or s != events[i - 1]["status"]]
    assert transitions == ["fetching", "downloading", "processing", "completed"]

    final = events[-1]
    assert final["status"] == "completed"
    assert final["percent"] == 100.0
    assert final["title"] == "Fake Video"
    assert final["filepath"] == "/tmp/fake.mkv"

    # The bar must never move backwards over the life of the job.
    percents = [e["percent"] for e in events]
    assert percents == sorted(percents), percents


def test_events_404_for_unknown_job(client):
    assert client.get("/api/jobs/nope/events").status_code == 404


def test_cancel_unknown_job_is_404(client):
    assert client.post("/api/jobs/nope/cancel").status_code == 404


def test_file_endpoint_404s_when_file_is_gone(client, monkeypatch):
    monkeypatch.setattr("yt_downloader.web.jobs.VideoDownloader", FakeDownloader)
    FakeDownloader.release.set()
    job = client.post("/api/download", json={"url": "http://yt/abc"}).json()

    with client.stream("GET", f"/api/jobs/{job['id']}/events") as response:
        for line in response.iter_lines():
            if line.startswith("event: done"):
                break

    # FakeDownloader reports a path that was never written to disk.
    assert client.get(f"/api/jobs/{job['id']}/file").status_code == 404


def test_output_path_expands_user():
    from yt_downloader.web.jobs import safe_output_path

    assert not str(safe_output_path("~/vids", "./videos")).startswith("~")
    assert safe_output_path("", "./videos").is_absolute()
