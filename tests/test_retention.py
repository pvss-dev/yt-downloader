import time
from pathlib import Path

from yt_downloader.retention import (
    RetentionPolicy,
    RetentionScheduler,
    sweep,
)

DAY = 86400


def make(directory: Path, name: str, size: int = 1000, age_days: float = 0) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    when = time.time() - age_days * DAY
    import os

    os.utime(path, (when, when))
    return path


# --------------------------- safety by default ---------------------------

def test_does_nothing_without_limits(tmp_path):
    """Deleting a user's files by default would be the wrong trade."""
    old = make(tmp_path, "ancient.mkv", age_days=999)

    policy = RetentionPolicy(max_partial_age_hours=None)
    assert policy.is_active is False

    result = sweep(tmp_path, policy)
    assert result.deleted == []
    assert old.exists()


def test_ignores_files_this_project_never_creates(tmp_path):
    """A sweep must not touch unrelated files in the output folder."""
    keep = [
        make(tmp_path, "notes.docx", age_days=99),
        make(tmp_path, "photo.jpg", age_days=99),
        make(tmp_path, ".env", age_days=99),
        make(tmp_path, "script.py", age_days=99),
    ]
    doomed = make(tmp_path, "video.mkv", age_days=99)

    sweep(tmp_path, RetentionPolicy(max_age_days=1))

    assert all(p.exists() for p in keep)
    assert not doomed.exists()


def test_transcripts_are_kept_unless_asked(tmp_path):
    """The .txt is the cheap, valuable artifact; the video is the bulk."""
    transcript = make(tmp_path, "aula.txt", age_days=99)
    video = make(tmp_path, "aula.mkv", age_days=99)

    sweep(tmp_path, RetentionPolicy(max_age_days=1))
    assert transcript.exists()
    assert not video.exists()

    sweep(tmp_path, RetentionPolicy(max_age_days=1, include_transcripts=True))
    assert not transcript.exists()


def test_symlinks_are_never_followed(tmp_path):
    """Following one could delete a file outside the managed directory."""
    outside = tmp_path / "outside"
    outside.mkdir()
    real = make(outside, "precious.mkv", age_days=99)

    managed = tmp_path / "videos"
    managed.mkdir()
    (managed / "link.mkv").symlink_to(real)

    sweep(managed, RetentionPolicy(max_age_days=1))

    assert real.exists(), "a symlink target outside the folder must survive"


def test_dry_run_reports_without_deleting(tmp_path):
    video = make(tmp_path, "a.mkv", size=5_000_000, age_days=99)

    result = sweep(tmp_path, RetentionPolicy(max_age_days=1), dry_run=True)

    assert video.exists()
    assert result.dry_run is True
    assert result.deleted == [video]
    assert result.freed_bytes == 5_000_000
    assert "Would delete" in result.summary()


# --------------------------- the limits ---------------------------

def test_age_limit(tmp_path):
    fresh = make(tmp_path, "new.mkv", age_days=1)
    old = make(tmp_path, "old.mkv", age_days=10)

    sweep(tmp_path, RetentionPolicy(max_age_days=7))

    assert fresh.exists()
    assert not old.exists()


def test_count_limit_keeps_the_newest(tmp_path):
    files = [make(tmp_path, f"v{i}.mkv", age_days=i) for i in range(5)]

    sweep(tmp_path, RetentionPolicy(max_files=2))

    assert files[0].exists() and files[1].exists()
    assert not any(p.exists() for p in files[2:])


def test_size_limit_drops_oldest_first(tmp_path):
    newest = make(tmp_path, "c.mkv", size=600, age_days=1)
    middle = make(tmp_path, "b.mkv", size=600, age_days=2)
    oldest = make(tmp_path, "a.mkv", size=600, age_days=3)

    sweep(tmp_path, RetentionPolicy(max_total_bytes=1200))

    assert newest.exists()
    assert middle.exists()
    assert not oldest.exists()


def test_limits_compose(tmp_path):
    recent_big = make(tmp_path, "recent.mkv", size=900, age_days=1)
    old_small = make(tmp_path, "old.mkv", size=100, age_days=30)

    sweep(tmp_path, RetentionPolicy(max_age_days=7, max_total_bytes=10_000))

    assert recent_big.exists()
    assert not old_small.exists(), "violating any single limit is enough"


def test_stale_partial_files_go_even_without_other_limits(tmp_path):
    """Interrupted downloads leave .part files that nothing will resume."""
    stale = make(tmp_path, "broken.mkv.part", age_days=3)
    fresh = make(tmp_path, "downloading.mkv.part", age_days=0)
    video = make(tmp_path, "keep.mkv", age_days=365)

    # No size/age/count limits at all -- only the .part rule applies.
    sweep(tmp_path, RetentionPolicy(max_partial_age_hours=24))

    assert not stale.exists()
    assert fresh.exists()
    assert video.exists(), "a finished video must survive the partial cleanup"


def test_subdirectories_are_swept(tmp_path):
    """Playlists download into their own folder."""
    nested = make(tmp_path, "Minha Playlist/01 - intro.mkv", age_days=99)
    sweep(tmp_path, RetentionPolicy(max_age_days=1))
    assert not nested.exists()


# --------------------------- in-flight jobs ---------------------------

def test_running_jobs_are_protected(tmp_path):
    """Sweeping a file a job is still writing would break that job."""
    active = make(tmp_path, "in-progress.mkv", age_days=99)
    idle = make(tmp_path, "finished.mkv", age_days=99)

    sweep(tmp_path, RetentionPolicy(max_age_days=1), protected=[active])

    assert active.exists()
    assert not idle.exists()


def test_protected_files_still_count_against_the_quota(tmp_path):
    """Otherwise an active download could push the folder over its limit."""
    active = make(tmp_path, "active.mkv", size=1000, age_days=0)
    older = make(tmp_path, "older.mkv", size=1000, age_days=1)

    sweep(tmp_path, RetentionPolicy(max_total_bytes=1500), protected=[active])

    assert active.exists()
    assert not older.exists()


# --------------------------- reporting ---------------------------

def test_result_reports_freed_space(tmp_path):
    make(tmp_path, "a.mkv", size=2_000_000_000, age_days=99)
    result = sweep(tmp_path, RetentionPolicy(max_age_days=1), dry_run=True)
    assert result.freed_display == "2.00 GB"


def test_missing_directory_is_not_an_error(tmp_path):
    result = sweep(tmp_path / "does-not-exist", RetentionPolicy(max_age_days=1))
    assert result.deleted == []


def test_policy_description_is_human_readable():
    policy = RetentionPolicy(max_age_days=30, max_total_bytes=50_000_000_000)
    described = policy.describe()
    assert "30 days" in described
    assert "50.0 GB" in described
    assert RetentionPolicy(max_partial_age_hours=None).describe() == "no limits set"


# --------------------------- scheduler ---------------------------

def test_scheduler_refuses_to_start_without_limits(tmp_path):
    scheduler = RetentionScheduler(tmp_path, RetentionPolicy(max_partial_age_hours=None))
    assert scheduler.start() is False
    assert scheduler.running is False


def test_scheduler_runs_a_sweep_on_demand(tmp_path):
    old = make(tmp_path, "old.mkv", age_days=99)
    scheduler = RetentionScheduler(tmp_path, RetentionPolicy(max_age_days=1))

    result = scheduler.run_once()

    assert not old.exists()
    assert scheduler.last_result is result
    assert len(result.deleted) == 1


def test_scheduler_consults_protected_paths_each_sweep(tmp_path):
    active = make(tmp_path, "active.mkv", age_days=99)
    scheduler = RetentionScheduler(
        tmp_path,
        RetentionPolicy(max_age_days=1),
        protected_paths=lambda: [active],
    )

    scheduler.run_once()
    assert active.exists()


# --------------------------- CLI ---------------------------

def test_cli_refuses_and_deletes_nothing_without_limits(tmp_path, capsys):
    """Running the command to see what it does must never cost a file."""
    from yt_downloader.cleanup_cli import main

    video = make(tmp_path, "old.mkv", age_days=99)
    stale = make(tmp_path, "broken.mkv.part", age_days=99)

    assert main(["-o", str(tmp_path)]) == 2
    assert "No limits given" in capsys.readouterr().err
    assert video.exists()
    assert stale.exists(), "not even a stale .part goes without an explicit limit"


def test_cli_dry_run_leaves_everything(tmp_path, capsys):
    from yt_downloader.cleanup_cli import main

    video = make(tmp_path, "old.mkv", age_days=99)
    assert main(["-o", str(tmp_path), "--days", "30", "--dry-run"]) == 0
    assert video.exists()
    assert "Would delete" in capsys.readouterr().out


def test_cli_applies_the_limit(tmp_path):
    from yt_downloader.cleanup_cli import main

    old = make(tmp_path, "old.mkv", age_days=99)
    new = make(tmp_path, "new.mkv", age_days=1)

    assert main(["-o", str(tmp_path), "--days", "30", "-q"]) == 0
    assert not old.exists()
    assert new.exists()


def test_cli_keep_partials(tmp_path):
    from yt_downloader.cleanup_cli import main

    stale = make(tmp_path, "broken.mkv.part", age_days=99)
    main(["-o", str(tmp_path), "--keep", "50", "--keep-partials", "-q"])
    assert stale.exists()
