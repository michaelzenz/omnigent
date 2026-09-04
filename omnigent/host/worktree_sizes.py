"""Async worktree size calculation with CPU/IO throttling.

Calculates on-disk size of each git worktree belonging to a repository,
using ``du`` with ``nice``/``ionice`` to limit CPU and IO impact. Results
are cached per repo root and never expire — the 10-minute interval only
controls when a background recalculation is triggered, not whether
cached data is returned. See designs/ASYNC_WORKTREE_SIZES.md.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

from omnigent.host.git_worktree import (
    WorktreeError,
    _locked_auto_cache,
    _run_git,
    list_worktrees,
)

_logger = logging.getLogger(__name__)

_RECALC_INTERVAL_S = 600.0  # 10 minutes
_DU_TIMEOUT_S = 300.0  # 5 minutes per worktree


def _worktree_has_dirty_files(path: str) -> bool:
    """Check if a worktree has modified, staged, or untracked (non-ignored) files.

    Ignored files (e.g. ``__pycache__``) don't count — they don't conflict
    with branch switching and are present after almost every session.
    This matches the ``_worktree_is_clean`` gate used by the reuse path.
    """
    result = _run_git(
        ["status", "--porcelain=v1", "--untracked-files=all"],
        cwd=path,
    )
    if result.returncode != 0:
        return False
    return result.stdout.strip() != ""


def _auto_managed_info(repo_root: str) -> dict[str, dict[str, object]]:
    """Lease info for Omnigent-managed auto worktrees belonging to ``repo_root``.

    Returns a dict mapping worktree path to its registry entry, which
    contains ``lease_owner``, ``lease_expires_at``, and ``health``.
    """
    try:
        with _locked_auto_cache() as entries:
            return {
                path: raw
                for path, raw in entries.items()
                if isinstance(path, str)
                and isinstance(raw, dict)
                and raw.get("repo_root") == repo_root
            }
    except Exception:
        _logger.warning("failed to read auto worktree cache", exc_info=True)
        return {}


@dataclass
class WorktreeSizeEntry:
    """One worktree's size result.

    :param path: Absolute worktree directory.
    :param branch: Checked-out branch, or None for detached HEAD.
    :param is_main: True for the repository's main work tree.
    :param size_bytes: Total bytes on disk (0 when du failed).
    :param dirty: True when the worktree has modified/staged tracked files.
    :param managed: True when the worktree is an Omnigent-managed auto worktree.
    :param reusable: True when auto-new-worktree can reuse this worktree
        (managed, not the main worktree, and clean).
    :param error: Per-worktree error message, or None on success.
    """

    path: str
    branch: str | None
    is_main: bool
    size_bytes: int
    dirty: bool = False
    managed: bool = False
    reusable: bool = False
    error: str | None = None


@dataclass
class WorktreeSizeResult:
    """Cached size calculation result for a repository.

    :param repo_root: Absolute path of the main work tree.
    :param entries: One per worktree, main first.
    :param total_bytes: Sum of non-failed worktree sizes.
    :param calculated_at: Monotonic timestamp of the calculation.
    :param error: Overall error (e.g. "not a git repo"), or None.
    """

    repo_root: str
    entries: list[WorktreeSizeEntry]
    total_bytes: int
    calculated_at: float
    error: str | None = None


class WorktreeSizeCache:
    """Thread-safe in-memory cache keyed by repo root. Never expires.

    Cached data is always returned regardless of age. The 10-minute
    ``recalc_interval_s`` only controls when a background recalculation
    is triggered — it does not invalidate the cache.
    """

    def __init__(self, recalc_interval_s: float = _RECALC_INTERVAL_S) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, WorktreeSizeResult] = {}
        self._recalc_interval_s = recalc_interval_s
        self._in_flight: set[str] = set()

    def get(self, repo_root: str) -> WorktreeSizeResult | None:
        with self._lock:
            return self._cache.get(repo_root)

    def put(self, repo_root: str, result: WorktreeSizeResult) -> None:
        with self._lock:
            self._cache[repo_root] = result

    def needs_recalc(self, repo_root: str) -> bool:
        with self._lock:
            entry = self._cache.get(repo_root)
            if entry is None:
                return True
            return (time.monotonic() - entry.calculated_at) >= self._recalc_interval_s

    def mark_in_flight(self, repo_root: str) -> bool:
        with self._lock:
            if repo_root in self._in_flight:
                return False
            self._in_flight.add(repo_root)
            return True

    def clear_in_flight(self, repo_root: str) -> None:
        with self._lock:
            self._in_flight.discard(repo_root)


def _dir_size_bytes(path: str) -> tuple[int, str | None]:
    """Calculate directory size with CPU/IO throttling.

    Returns (size_bytes, error). Uses nice/ionice to limit impact.
    """
    cmd: list[str] = ["du", "-sb", path]
    if sys.platform == "linux":
        cmd = ["nice", "-n", "19", "ionice", "-c", "3"] + cmd
    elif sys.platform == "darwin":
        cmd = ["nice", "-n", "19", "du", "-sk", path]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_DU_TIMEOUT_S, check=False
        )
    except subprocess.TimeoutExpired:
        return 0, f"du timed out after {_DU_TIMEOUT_S:.0f}s"
    except FileNotFoundError:
        return 0, "du not found on host"
    if result.returncode != 0:
        detail = result.stderr.strip()[:200]
        return 0, f"du failed (exit {result.returncode}){f': {detail}' if detail else ''}"
    try:
        size_str = result.stdout.split("\t")[0].strip()
        if sys.platform == "darwin":
            return int(size_str) * 1024, None
        return int(size_str), None
    except (ValueError, IndexError):
        return 0, "du output could not be parsed"


def calculate_worktree_sizes(repo_path: str) -> WorktreeSizeResult:
    """List worktrees and calculate each one's on-disk size.

    :param repo_path: Absolute path inside a git repository.
    :returns: WorktreeSizeResult with per-worktree sizes.
    """
    try:
        worktrees = list_worktrees(repo_path=repo_path)
    except WorktreeError as exc:
        return WorktreeSizeResult(
            repo_root=repo_path,
            entries=[],
            total_bytes=0,
            calculated_at=time.monotonic(),
            error=exc.message,
        )

    entries: list[WorktreeSizeEntry] = []
    total_bytes = 0
    repo_root = worktrees[0].path if worktrees else repo_path
    managed_info = _auto_managed_info(repo_root)
    now = int(time.time())
    for wt in worktrees:
        size, err = _dir_size_bytes(wt.path)
        dirty = False
        info = managed_info.get(wt.path)
        managed = info is not None
        # A worktree is reusable when: managed, not main, clean, and the
        # lease is free (no owner or lease expired).
        lease_free = False
        if managed and not wt.is_main and err is None:
            dirty = _worktree_has_dirty_files(wt.path)
            if not dirty:
                owner = info.get("lease_owner") if isinstance(info, dict) else None
                expires_at = info.get("lease_expires_at") if isinstance(info, dict) else None
                lease_free = owner is None or (
                    isinstance(expires_at, int) and expires_at <= now
                )
        entries.append(
            WorktreeSizeEntry(
                path=wt.path,
                branch=wt.branch,
                is_main=wt.is_main,
                size_bytes=size,
                dirty=dirty,
                managed=managed,
                reusable=managed and not wt.is_main and not dirty and err is None and lease_free,
                error=err,
            )
        )
        if err is None:
            total_bytes += size

    return WorktreeSizeResult(
        repo_root=repo_root,
        entries=entries,
        total_bytes=total_bytes,
        calculated_at=time.monotonic(),
    )
