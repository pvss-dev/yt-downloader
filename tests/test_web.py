import json
import threading
from pathlib import Path

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


# --------------------------- transcription ---------------------------

def test_health_reports_transcription_availability(client):
    body = client.get("/api/health").json()
    assert "transcription_available" in body
    assert "small" in body["whisper_models"]


def test_download_rejects_transcribe_when_whisper_missing(client, monkeypatch):
    """Fail at submit with install instructions, not silently mid-job."""
    monkeypatch.setattr(server.Transcriber, "is_available", staticmethod(lambda: False))

    response = client.post("/api/download", json={"url": "http://yt/abc", "transcribe": True})
    assert response.status_code == 400
    assert "[transcribe]" in response.json()["detail"]


def test_download_rejects_impossible_model_task_combo(client, monkeypatch):
    monkeypatch.setattr(server.Transcriber, "is_available", staticmethod(lambda: True))

    response = client.post("/api/download", json={
        "url": "http://yt/abc", "transcribe": True, "whisper_model": "nonexistent",
    })
    assert response.status_code == 400
    assert "Unknown Whisper model" in response.json()["detail"]


def test_transcription_stage_streams_and_saves(client, monkeypatch, tmp_path):
    monkeypatch.setattr("yt_downloader.web.jobs.VideoDownloader", FakeDownloader)
    monkeypatch.setattr(server.Transcriber, "is_available", staticmethod(lambda: True))

    transcript = tmp_path / "fake.txt"
    transcript.write_text("linha um\nlinha dois", encoding="utf-8")

    class FakeService:
        def __init__(self, config, on_transcribe_progress=None):
            self.on_progress = on_transcribe_progress

        def process(self, path, write_srt=False):
            from yt_downloader.transcription.service import TranscriptionOutcome
            from yt_downloader.transcription.transcriber import (
                TranscriptionProgress,
                TranscriptionResult,
            )

            self.on_progress(TranscriptionProgress("loading_model", model="tiny", device="cpu"))
            self.on_progress(TranscriptionProgress(
                "transcribing", percent=50.0, seconds_done=30, seconds_total=60,
            ))
            return TranscriptionOutcome(
                success=True,
                result=TranscriptionResult(text="linha um\nlinha dois", language="pt"),
                transcript_path=transcript,
            )

    monkeypatch.setattr("yt_downloader.transcription.TranscriptionService", FakeService)

    # Hold the job until the stream is open, or it finishes before we subscribe
    # and only the final snapshot is replayed.
    FakeDownloader.release.clear()
    job = client.post("/api/download", json={
        "url": "http://yt/abc", "transcribe": True, "whisper_model": "tiny",
    }).json()
    assert job["transcribe"] is True

    events = []
    with client.stream("GET", f"/api/jobs/{job['id']}/events") as response:
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            events.append(json.loads(line[5:].strip()))
            FakeDownloader.release.set()
            if events[-1]["status"] in ("completed", "error", "cancelled"):
                break

    statuses = {e["status"] for e in events}
    assert {"loading_model", "transcribing"} <= statuses

    final = events[-1]
    assert final["status"] == "completed"
    assert final["detected_language"] == "pt"
    assert final["transcript_path"] == str(transcript)
    assert "linha um" in final["transcript_preview"]

    # ...and the transcript is downloadable as plain text.
    served = client.get(f"/api/jobs/{job['id']}/transcript")
    assert served.status_code == 200
    assert "linha dois" in served.text


def test_transcript_404_when_job_has_none(client, monkeypatch):
    monkeypatch.setattr("yt_downloader.web.jobs.VideoDownloader", FakeDownloader)
    FakeDownloader.release.set()
    job = client.post("/api/download", json={"url": "http://yt/abc"}).json()
    assert client.get(f"/api/jobs/{job['id']}/transcript").status_code == 404


# --------------------------- uploads ---------------------------

@pytest.fixture
def fake_transcription(monkeypatch, tmp_path):
    """Replace Whisper with a stub that writes a transcript where asked."""
    monkeypatch.setattr(server.Transcriber, "is_available", staticmethod(lambda: True))

    # Held closed until the test subscribes, so the SSE stream is exercised
    # against a running job rather than a finished one.
    gate = threading.Event()

    class FakeService:
        release = gate

        def __init__(self, config, on_transcribe_progress=None):
            self.on_progress = on_transcribe_progress

        def process(self, path, output_file=None, write_srt=False):
            gate.wait(timeout=5)
            from yt_downloader.transcription.service import TranscriptionOutcome
            from yt_downloader.transcription.transcriber import (
                TranscriptionProgress,
                TranscriptionResult,
            )

            self.on_progress(TranscriptionProgress("loading_model", model="tiny"))
            self.on_progress(TranscriptionProgress("transcribing", percent=100.0))

            target = Path(output_file)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("texto transcrito", encoding="utf-8")
            return TranscriptionOutcome(
                success=True,
                result=TranscriptionResult(text="texto transcrito", language="pt"),
                transcript_path=target,
            )

    monkeypatch.setattr("yt_downloader.transcription.TranscriptionService", FakeService)
    # Tests that only care about the outcome let it run immediately; the one
    # asserting on stage order clears this first.
    gate.set()
    return tmp_path


def _drain(client, job_id, release=None):
    events = []
    with client.stream("GET", f"/api/jobs/{job_id}/events") as response:
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            events.append(json.loads(line[5:].strip()))
            if release is not None:
                release.set()
            if events[-1]["status"] in ("completed", "error", "cancelled"):
                break
    return events


def test_upload_transcribes_without_a_download_stage(client, fake_transcription, monkeypatch):
    from yt_downloader.transcription import TranscriptionService as Fake

    Fake.release.clear()  # hold the job until the stream is open

    out = fake_transcription / "saida"
    response = client.post(
        "/api/upload",
        files={"file": ("aula.mp4", b"fake media bytes", "video/mp4")},
        data={"whisper_model": "tiny", "language": "pt", "output_path": str(out)},
    )
    assert response.status_code == 200
    job = response.json()
    assert job["is_upload"] is True
    assert job["source_name"] == "aula.mp4"

    events = _drain(client, job["id"], release=Fake.release)
    statuses = [e["status"] for e in events]

    # No download happens for an uploaded file.
    assert "downloading" not in statuses
    assert "loading_model" in statuses

    final = events[-1]
    assert final["status"] == "completed"
    assert final["detected_language"] == "pt"
    assert Path(final["transcript_path"]) == out / "aula.txt"
    assert (out / "aula.txt").read_text(encoding="utf-8") == "texto transcrito"


def test_upload_deletes_the_stored_media_afterwards(client, fake_transcription):
    response = client.post(
        "/api/upload",
        files={"file": ("aula.mp4", b"fake media bytes", "video/mp4")},
        data={"output_path": str(fake_transcription / "o")},
    )
    job_id = response.json()["id"]
    stored = Path(server.jobs.get(job_id).local_path)

    final = _drain(client, job_id)[-1]

    assert final["status"] == "completed"
    assert not stored.exists(), "the uploaded copy must not linger on the server"
    assert not stored.parent.exists()
    # No snapshot may ever pair a terminal status with a media file that is
    # already gone, or the download button 404s.
    assert final["filepath"] is None


def test_upload_strips_directories_from_the_filename(client, fake_transcription):
    """A crafted filename must not write outside the upload directory."""
    response = client.post(
        "/api/upload",
        files={"file": ("../../../../tmp/evil.mp4", b"bytes", "video/mp4")},
        data={"output_path": str(fake_transcription / "o")},
    )
    job = response.json()
    assert job["source_name"] == "evil.mp4"

    stored = Path(server.jobs.get(job["id"]).local_path)
    assert stored.parent.parent == server.UPLOAD_ROOT


def test_upload_rejects_empty_file(client, fake_transcription):
    response = client.post(
        "/api/upload", files={"file": ("empty.mp4", b"", "video/mp4")},
    )
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_upload_rejects_bad_model(client, fake_transcription):
    response = client.post(
        "/api/upload",
        files={"file": ("a.mp4", b"bytes", "video/mp4")},
        data={"whisper_model": "gigantic"},
    )
    assert response.status_code == 400
    assert "Unknown Whisper model" in response.json()["detail"]


def test_upload_refused_without_whisper(client, monkeypatch):
    monkeypatch.setattr(server.Transcriber, "is_available", staticmethod(lambda: False))
    response = client.post(
        "/api/upload", files={"file": ("a.mp4", b"bytes", "video/mp4")},
    )
    assert response.status_code == 400
    assert "[transcribe]" in response.json()["detail"]


def test_output_path_expands_user():
    from yt_downloader.web.jobs import safe_output_path

    assert not str(safe_output_path("~/vids", "./videos")).startswith("~")
    assert safe_output_path("", "./videos").is_absolute()


# --------------------------- output confinement ---------------------------

def test_output_root_confines_absolute_paths(tmp_path):
    """Without this, any request could write to /etc, ~/.ssh or cron."""
    from yt_downloader.web.jobs import OutputPathRejected, safe_output_path

    root = tmp_path / "media"
    root.mkdir()

    inside = safe_output_path(str(root / "series"), "./videos", str(root))
    assert inside == root / "series"

    for escape in ("/etc", "/root/.ssh", str(tmp_path / "elsewhere")):
        with pytest.raises(OutputPathRejected):
            safe_output_path(escape, "./videos", str(root))


def test_output_root_defeats_traversal(tmp_path):
    from yt_downloader.web.jobs import OutputPathRejected, safe_output_path

    root = tmp_path / "media"
    root.mkdir()

    with pytest.raises(OutputPathRejected):
        safe_output_path("../../etc", "./videos", str(root))

    # A relative path is taken as relative to the root, not the process cwd.
    assert safe_output_path("shows", "./videos", str(root)) == root / "shows"


def test_output_root_allows_the_root_itself(tmp_path):
    from yt_downloader.web.jobs import safe_output_path

    root = tmp_path / "media"
    root.mkdir()
    assert safe_output_path(str(root), "./videos", str(root)) == root


def test_download_rejects_escaping_output_path(client, monkeypatch, tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    monkeypatch.setattr(server, "OUTPUT_ROOT", str(root))

    response = client.post(
        "/api/download", json={"url": "http://yt/abc", "output_path": "/etc"},
    )
    assert response.status_code == 400
    assert "must be inside" in response.json()["detail"]


def test_upload_rejects_escaping_output_path(client, monkeypatch, tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    monkeypatch.setattr(server, "OUTPUT_ROOT", str(root))
    monkeypatch.setattr(server.Transcriber, "is_available", staticmethod(lambda: True))

    response = client.post(
        "/api/upload",
        files={"file": ("a.mp4", b"bytes", "video/mp4")},
        data={"output_path": "/etc"},
    )
    assert response.status_code == 400
    # The rejected upload must not be left behind on disk.
    assert not any(server.UPLOAD_ROOT.glob("*/a.mp4")) if server.UPLOAD_ROOT.exists() else True


# --------------------------- session isolation ---------------------------
#
# Each TestClient keeps its own cookie jar, so two of them are two visitors.

@pytest.fixture
def other_client():
    return TestClient(server.app)


def _finished_job(client, monkeypatch=None):
    FakeDownloader.release.set()
    job = client.post("/api/download", json={"url": "http://yt/abc"}).json()
    with client.stream("GET", f"/api/jobs/{job['id']}/events") as response:
        for line in response.iter_lines():
            if line.startswith("event: done"):
                break
    return job


def test_each_visitor_gets_a_session_cookie(client):
    response = client.get("/api/jobs")
    assert server.SESSION_COOKIE in response.cookies
    # httponly keeps it out of reach of page scripts.
    assert "httponly" in response.headers["set-cookie"].lower()


def test_visitors_do_not_see_each_others_jobs(client, other_client, monkeypatch):
    monkeypatch.setattr("yt_downloader.web.jobs.VideoDownloader", FakeDownloader)

    mine = _finished_job(client)

    assert [j["id"] for j in client.get("/api/jobs").json()["jobs"]] == [mine["id"]]
    assert other_client.get("/api/jobs").json()["jobs"] == []


def test_a_visitor_cannot_download_another_visitors_file(client, other_client, monkeypatch, tmp_path):
    monkeypatch.setattr("yt_downloader.web.jobs.VideoDownloader", FakeDownloader)

    media = tmp_path / "mine.mkv"
    media.write_bytes(b"secret")
    mine = _finished_job(client)
    server.jobs.get(mine["id"]).filepath = str(media)

    assert client.get(f"/api/jobs/{mine['id']}/file").status_code == 200
    # 404, not 403: a stranger must not learn the job even exists.
    assert other_client.get(f"/api/jobs/{mine['id']}/file").status_code == 404


def test_a_visitor_cannot_cancel_another_visitors_job(client, other_client, monkeypatch):
    monkeypatch.setattr("yt_downloader.web.jobs.VideoDownloader", FakeDownloader)

    FakeDownloader.release.clear()
    mine = client.post("/api/download", json={"url": "http://yt/abc"}).json()
    try:
        assert other_client.post(f"/api/jobs/{mine['id']}/cancel").status_code == 404
        assert server.jobs.get(mine["id"]).status != "cancelling"
    finally:
        FakeDownloader.release.set()


def test_a_visitor_cannot_read_another_visitors_progress(client, other_client, monkeypatch):
    monkeypatch.setattr("yt_downloader.web.jobs.VideoDownloader", FakeDownloader)

    mine = _finished_job(client)
    assert other_client.get(f"/api/jobs/{mine['id']}/events").status_code == 404


def test_clearing_only_removes_your_own_jobs(client, other_client, monkeypatch):
    monkeypatch.setattr("yt_downloader.web.jobs.VideoDownloader", FakeDownloader)

    mine = _finished_job(client)
    theirs = _finished_job(other_client)

    assert other_client.delete("/api/jobs").json()["cleared"] == 1
    assert server.jobs.get(mine["id"]) is not None
    assert server.jobs.get(theirs["id"]) is None


# --------------------------- public mode ---------------------------

def test_public_mode_ignores_a_requested_output_path(client, monkeypatch, tmp_path):
    """A visitor must not choose where files land on someone else's server."""
    monkeypatch.setattr("yt_downloader.web.jobs.VideoDownloader", FakeDownloader)
    monkeypatch.setattr(server, "PUBLIC_MODE", True)
    monkeypatch.setattr(server, "OUTPUT_ROOT", str(tmp_path))
    FakeDownloader.release.set()

    job = client.post(
        "/api/download",
        json={"url": "http://yt/abc", "output_path": str(tmp_path / "chosen")},
    ).json()

    landed = Path(server.jobs.get(job["id"]).output_path)
    assert landed != tmp_path / "chosen", "the requested path must be ignored"
    assert landed == tmp_path / "videos", "it falls back to the server default"


def test_public_mode_hides_the_retention_endpoint(client, monkeypatch):
    monkeypatch.setattr(server, "PUBLIC_MODE", True)
    assert client.post("/api/retention/sweep").status_code == 404


def test_public_mode_reports_itself_in_health(client, monkeypatch):
    monkeypatch.setattr(server, "PUBLIC_MODE", True)
    body = client.get("/api/health").json()
    assert body["public_mode"] is True
    # The server-side path is not a visitor's business.
    assert body["output_root"] is None


def test_quota_rejects_a_flood_from_one_session(client, monkeypatch):
    from yt_downloader.web.jobs import JobManager

    monkeypatch.setattr("yt_downloader.web.jobs.VideoDownloader", FakeDownloader)
    monkeypatch.setattr(server, "jobs", JobManager(max_jobs_per_session_hour=2))
    FakeDownloader.release.set()

    for _ in range(2):
        assert client.post("/api/download", json={"url": "http://yt/a"}).status_code == 200

    third = client.post("/api/download", json={"url": "http://yt/a"})
    assert third.status_code == 429
    assert "per hour" in third.json()["detail"]


def test_quota_is_per_session_not_global(client, other_client, monkeypatch):
    from yt_downloader.web.jobs import JobManager

    monkeypatch.setattr("yt_downloader.web.jobs.VideoDownloader", FakeDownloader)
    monkeypatch.setattr(server, "jobs", JobManager(max_jobs_per_session_hour=1))
    FakeDownloader.release.set()

    assert client.post("/api/download", json={"url": "http://yt/a"}).status_code == 200
    assert client.post("/api/download", json={"url": "http://yt/a"}).status_code == 429
    # A different visitor still has their own allowance.
    assert other_client.post("/api/download", json={"url": "http://yt/a"}).status_code == 200
