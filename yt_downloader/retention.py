"""Automatic cleanup of downloaded media and transcripts.

Nothing here runs unless a limit is set: deleting a user's files by default
would be the wrong trade in every case. When enabled, a sweep removes the
oldest files that exceed whichever limits are configured.
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

logger = logging.getLogger(__name__)

# Only files this project produces are ever considered. A sweep must not touch
# whatever else happens to live in the output directory.
MEDIA_SUFFIXES = frozenset({
    ".mkv", ".mp4", ".webm", ".avi", ".mov", ".flv",
    ".mp3", ".m4a", ".opus", ".flac", ".wav", ".ogg", ".aac",
})
TRANSCRIPT_SUFFIXES = frozenset({".txt", ".srt", ".vtt"})
PARTIAL_SUFFIXES = frozenset({".part", ".ytdl", ".temp"})

SWEEPABLE = MEDIA_SUFFIXES | TRANSCRIPT_SUFFIXES | PARTIAL_SUFFIXES


@dataclass
class RetentionPolicy:
    """Limits on what may accumulate in the output directory.

    Every limit is optional and they compose: a file is deleted if it violates
    any one of them. With all of them unset, `is_active` is False and no sweep
    ever runs.
    """

    max_age_days: Optional[float] = None
    max_files: Optional[int] = None
    max_total_bytes: Optional[int] = None
    # Interrupted downloads leave .part files behind; these are stale far
    # sooner than a finished video.
    max_partial_age_hours: Optional[float] = 24.0
    include_transcripts: bool = False

    @property
    def is_active(self) -> bool:
        return any((
            self.max_age_days is not None,
            self.max_files is not None,
            self.max_total_bytes is not None,
        ))

    def describe(self) -> str:
        parts = []
        if self.max_age_days is not None:
            parts.append(f"older than {self.max_age_days:g} days")
        if self.max_files is not None:
            parts.append(f"beyond the newest {self.max_files}")
        if self.max_total_bytes is not None:
            parts.append(f"over {self.max_total_bytes / 1e9:.1f} GB total")
        return "; ".join(parts) or "no limits set"


@dataclass
class SweepResult:
    """What a sweep did, or would have done in dry-run mode."""

    deleted: list[Path] = field(default_factory=list)
    freed_bytes: int = 0
    kept: int = 0
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def freed_display(self) -> str:
        if self.freed_bytes >= 1e9:
            return f"{self.freed_bytes / 1e9:.2f} GB"
        return f"{self.freed_bytes / 1e6:.1f} MB"

    def summary(self) -> str:
        verb = "Would delete" if self.dry_run else "Deleted"
        return (
            f"{verb} {len(self.deleted)} file(s), freeing {self.freed_display}; "
            f"{self.kept} kept"
        )


def _candidates(directory: Path, policy: RetentionPolicy) -> list[Path]:
    """Files eligible for deletion, newest first."""
    suffixes = set(MEDIA_SUFFIXES | PARTIAL_SUFFIXES)
    if policy.include_transcripts:
        suffixes |= TRANSCRIPT_SUFFIXES

    found = []
    for path in directory.rglob("*"):
        # is_symlink first: a symlink to elsewhere must never be followed, or a
        # sweep could delete a file outside the directory it manages.
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix.lower() not in suffixes:
            continue
        try:
            found.append((path.stat().st_mtime, path))
        except OSError:
            continue

    found.sort(key=lambda pair: pair[0], reverse=True)
    return [path for _, path in found]


def _select_for_deletion(
        files: list[Path],
        policy: RetentionPolicy,
        now: float,
        protected: frozenset[Path],
) -> list[Path]:
    """Decide which files violate the policy. `files` is newest first."""
    doomed: list[Path] = []
    running_total = 0
    survivors = 0

    for index, path in enumerate(files):
        if path in protected:
            # A job is still writing this; sweeping it would break that job.
            try:
                running_total += path.stat().st_size
            except OSError:
                pass
            survivors += 1
            continue

        try:
            stat = path.stat()
        except OSError:
            continue

        age_seconds = now - stat.st_mtime
        is_partial = path.suffix.lower() in PARTIAL_SUFFIXES

        too_old = (
            policy.max_age_days is not None
            and age_seconds > policy.max_age_days * 86400
        )
        stale_partial = (
            is_partial
            and policy.max_partial_age_hours is not None
            and age_seconds > policy.max_partial_age_hours * 3600
        )
        # index counts every file including protected ones, so the "newest N"
        # limit reflects what is actually on disk.
        too_many = policy.max_files is not None and index >= policy.max_files
        over_quota = (
            policy.max_total_bytes is not None
            and running_total + stat.st_size > policy.max_total_bytes
        )

        if too_old or stale_partial or too_many or over_quota:
            doomed.append(path)
        else:
            running_total += stat.st_size
            survivors += 1

    return doomed


def sweep(
        directory: str | Path,
        policy: RetentionPolicy,
        dry_run: bool = False,
        protected: Iterable[str | Path] = (),
        now: Optional[float] = None,
) -> SweepResult:
    """Apply `policy` to `directory`.

    Args:
        directory: The folder to clean.
        policy: Limits to enforce. An inactive policy is a no-op.
        dry_run: Report what would go without removing anything.
        protected: Paths that must survive regardless (in-flight jobs).
        now: Reference timestamp, for testing.
    """
    result = SweepResult(dry_run=dry_run)

    if not policy.is_active and policy.max_partial_age_hours is None:
        return result

    directory = Path(directory).expanduser().resolve()
    if not directory.is_dir():
        return result

    guarded = frozenset(Path(p).expanduser().resolve() for p in protected)
    files = _candidates(directory, policy)
    doomed = _select_for_deletion(files, policy, now or time.time(), guarded)

    result.kept = len(files) - len(doomed)

    for path in doomed:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0

        if dry_run:
            result.deleted.append(path)
            result.freed_bytes += size
            continue

        try:
            path.unlink()
        except OSError as e:
            result.errors.append(f"{path.name}: {e}")
            continue

        result.deleted.append(path)
        result.freed_bytes += size

    if result.deleted:
        logger.info(f"Retention sweep: {result.summary()}")
    for problem in result.errors:
        logger.warning(f"Retention could not delete {problem}")

    return result


class RetentionScheduler:
    """Runs a sweep on an interval, in the background."""

    def __init__(
            self,
            directory: str | Path,
            policy: RetentionPolicy,
            interval_seconds: float = 3600.0,
            protected_paths: Optional[Callable[[], Iterable[str | Path]]] = None,
    ):
        self.directory = Path(directory)
        self.policy = policy
        self.interval_seconds = interval_seconds
        self.protected_paths = protected_paths or (lambda: ())
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_result: Optional[SweepResult] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Begin sweeping. Returns False when the policy sets no limits."""
        if not self.policy.is_active:
            return False
        if self.running:
            return True

        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(
            f"Retention active on {self.directory}: deleting files "
            f"{self.policy.describe()}, checked every "
            f"{self.interval_seconds / 60:.0f} min"
        )
        return True

    def stop(self) -> None:
        self._stop.set()

    def run_once(self, dry_run: bool = False) -> SweepResult:
        self.last_result = sweep(
            self.directory,
            self.policy,
            dry_run=dry_run,
            protected=self.protected_paths(),
        )
        return self.last_result

    def _loop(self) -> None:
        # Sweep on startup so a server that was down past the retention window
        # cleans up immediately rather than after a full interval.
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                logger.exception("Retention sweep failed")
            self._stop.wait(self.interval_seconds)
