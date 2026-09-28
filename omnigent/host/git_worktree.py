"""Host-side git worktree operations for session-start worktrees.

Runs ``git`` (via argv lists, never a shell) on the host in response to
``host.create_worktree`` / ``host.remove_worktree`` frames. Branch names
are validated against git ref-format rules before reaching argv. See
designs/SESSION_GIT_WORKTREE.md.
"""

from __future__ import annotations

import codecs
import errno
import json
import os
import re
import select
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import cast

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows uses the process lock below.
    fcntl = None  # type: ignore[assignment]
    import msvcrt
else:  # pragma: no cover - Windows-only module.
    msvcrt = None  # type: ignore[assignment]

# Materializing a large monorepo worktree can take an unbounded amount of
# time. Let git finish or fail naturally.
_GIT_TIMEOUT_S: float | None = None

_AUTO_LEASE_SECONDS = 86_400
# How long a folder's "preparing" reservation (claim taken before the
# minutes-long git work, which runs with the registry lock released) is
# honored before the pool reclaims it. Generous: a suspended host must not
# wake up to find its mid-flight folder switched underneath it.
_RESERVED_STALE_S = 6 * 3600
_AUTO_CACHE_PROCESS_LOCK = threading.RLock()

# Registry schema version. v2 stores folders (with a fencing ``seq``) and
# per-session leases separately; a v1 file fails loud — hand-migrate it to
# the v2 shape or delete it (a fresh empty registry is seeded), no fallback.
_REGISTRY_VERSION = 2

# Chars git refuses in a ref: space, control chars, ``~^:?*[\``, DEL.
# (``..``, leading ``-``/``.``, ``/`` edges, ``.lock``, ``@{`` are
# checked separately.)
_INVALID_BRANCH_CHARS = re.compile(r"[\x00-\x20~^:?*\[\\\x7f]")


class WorktreeError(Exception):
    """Raised when a git worktree operation fails.

    The message is user-facing and surfaced verbatim in the
    ``host.*_worktree_result`` frame's ``error`` field.

    :param message: Human-readable failure reason, e.g.
        ``"not a git repository: /tmp/x"``.
    """

    def __init__(self, message: str) -> None:
        """Initialize with the user-facing error message.

        :param message: Error string surfaced to the API caller.
        """
        super().__init__(message)
        self.message = message


def validate_branch_name(name: str) -> None:
    """Validate a git branch name against ``git check-ref-format`` rules.

    :param name: Proposed branch name, e.g. ``"feature/login"``.
    :raises WorktreeError: If the name is empty or violates any
        ref-format rule. The message names the specific violation.
    """
    if not name:
        raise WorktreeError("branch name must not be empty")
    if name.startswith("-"):
        raise WorktreeError(f"branch name must not start with '-': {name!r}")
    if name.startswith("/") or name.endswith("/"):
        raise WorktreeError(f"branch name must not start or end with '/': {name!r}")
    if name.endswith("."):
        raise WorktreeError(f"branch name must not end with '.': {name!r}")
    if any(part.endswith(".lock") for part in name.split("/")):
        raise WorktreeError(f"branch name path components must not end with '.lock': {name!r}")
    if ".." in name:
        raise WorktreeError(f"branch name must not contain '..': {name!r}")
    if "//" in name:
        raise WorktreeError(f"branch name must not contain '//': {name!r}")
    if "@{" in name:
        raise WorktreeError(f"branch name must not contain '@{{': {name!r}")
    if name == "@":
        raise WorktreeError("branch name must not be '@'")
    if _INVALID_BRANCH_CHARS.search(name):
        raise WorktreeError(
            f"branch name {name!r} contains an invalid character; spaces, "
            f"control characters, and any of ~ ^ : ? * [ \\ are not allowed"
        )
    # No path component may start with '.' (e.g. ".hidden" or "a/.b").
    if any(part.startswith(".") for part in name.split("/")):
        raise WorktreeError(f"branch name path components must not start with '.': {name!r}")


def _sanitize_repo_name(name: str) -> str:
    """Sanitize a repo directory name for use as a path segment.

    :param name: Last path segment of the repo root, e.g. ``"myrepo"``.
    :returns: Filesystem-safe single segment, e.g. ``"myrepo"``.
    """
    return re.sub(r"[^a-zA-Z0-9._-]", "-", name).strip("-") or "repo"


def _run_git(
    args: list[str],
    *,
    cwd: str,
) -> subprocess.CompletedProcess[str]:
    """Run a git command, returning the completed process.

    :param args: Git argv *after* ``git``, e.g.
        ``["rev-parse", "--show-toplevel"]``. Passed as a list so no
        shell parsing occurs.
    :param cwd: Working directory to run git in, e.g.
        ``"/Users/alice/myrepo"``.
    :returns: The completed process with captured text stdout/stderr.
    :raises WorktreeError: If git is not installed.
    """
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError as exc:
        if not Path(cwd).is_dir():
            raise WorktreeError(f"worktree directory does not exist: {cwd}") from exc
        raise WorktreeError("git is not installed on the host") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorktreeError("git command timed out") from exc


def _git_error(label: str, result: subprocess.CompletedProcess[str]) -> WorktreeError:
    """Build a WorktreeError from a failed git command.

    Includes the exit code (always present) and stderr when non-empty,
    so no invented "unknown error" fallback is needed.

    :param label: What failed, e.g. ``"git worktree add failed"``.
    :param result: The completed process with a non-zero return code.
    :returns: A :class:`WorktreeError` with code + stderr detail.
    """
    detail = result.stderr.strip()
    suffix = f": {detail}" if detail else ""
    return WorktreeError(f"{label} (exit {result.returncode}){suffix}")


def _main_work_tree(repo_path: str) -> str:
    """Resolve the MAIN work tree for any path inside a git repo.

    ``git worktree list --porcelain`` enumerates every work tree of the
    repository; its first entry is always the main one (the checkout all
    linked worktrees share). Run from ``repo_path``, this resolves the
    same main work tree whether the user picked the main checkout, a
    subdirectory, or a linked worktree.

    :param repo_path: Absolute path inside a git repository — the
        directory the user picked, e.g.
        ``"/Users/alice/myrepo-worktrees/feature"``.
    :returns: Absolute path of the main work tree, e.g.
        ``"/Users/alice/myrepo"``.
    :raises WorktreeError: If ``repo_path`` is not a directory or not
        inside a git work tree.
    """
    if not Path(repo_path).is_dir():
        raise WorktreeError(f"path is not a directory: {repo_path}")
    result = _run_git(["worktree", "list", "--porcelain"], cwd=repo_path)
    if result.returncode != 0:
        raise WorktreeError(f"not a git repository: {repo_path}")
    for line in result.stdout.splitlines():
        # Porcelain format: the first record's ``worktree <path>`` line is
        # the main work tree; linked worktrees follow.
        if line.startswith("worktree "):
            return line[len("worktree ") :].strip()
    raise WorktreeError(f"could not resolve main work tree for {repo_path}")


@dataclass
class WorktreeInfo:
    """One entry from ``git worktree list``.

    :param path: Absolute worktree directory, e.g.
        ``"/Users/alice/.omnigent/worktrees/feature-login"``.
    :param branch: Checked-out branch without the ``refs/heads/``
        prefix, e.g. ``"feature/login"``. ``None`` when the worktree
        is in detached-HEAD state.
    :param is_main: ``True`` for the repository's main work tree (the
        first ``git worktree list`` record), ``False`` for linked
        worktrees.
    :param detached: ``True`` when the worktree has a detached HEAD
        (no branch checked out).
    """

    path: str
    branch: str | None
    is_main: bool
    detached: bool


def list_worktrees(*, repo_path: str) -> list[WorktreeInfo]:
    """List the git worktrees of the repository containing ``repo_path``.

    Resolves the main work tree first (so a linked worktree resolves the
    same list as the main checkout), then parses
    ``git worktree list --porcelain``. The first record is always the
    main work tree; the rest are linked worktrees.

    :param repo_path: Absolute path inside a git repository — the
        directory the user picked, e.g. ``"/Users/alice/myrepo"``.
    :returns: One :class:`WorktreeInfo` per worktree, main first.
    :raises WorktreeError: If ``repo_path`` is not a directory or not
        inside a git work tree, or if ``git worktree list`` fails.
    """
    repo_root = _main_work_tree(repo_path)
    result = _run_git(["worktree", "list", "--porcelain"], cwd=repo_root)
    if result.returncode != 0:
        raise _git_error("git worktree list failed", result)

    worktrees: list[WorktreeInfo] = []
    path: str | None = None
    branch: str | None = None
    detached = False
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree ") :].strip()
            branch = None
            detached = False
        elif line.startswith("branch "):
            ref = line[len("branch ") :].strip()
            branch = ref[len("refs/heads/") :] if ref.startswith("refs/heads/") else ref
        elif line == "detached":
            detached = True
        elif line == "" and path is not None:
            # Blank line terminates a record.
            worktrees.append(
                WorktreeInfo(
                    path=path,
                    branch=branch,
                    is_main=not worktrees,
                    detached=detached,
                )
            )
            path = None
    # The porcelain output may omit a trailing blank line for the last record.
    if path is not None:
        worktrees.append(
            WorktreeInfo(path=path, branch=branch, is_main=not worktrees, detached=detached)
        )
    return worktrees


def _resolve_worktree_path(repo_root: str) -> Path:
    """Compute a unique Omnigent worktree directory path.

    Places the worktree at
    ``~/.omnigent/worktrees/<repo-name>/<repo-name>-<uuid>-<timestamp>``.
    The second-level timestamp can collide within one second, so a random
    uuid segment provides collision-free uniqueness without a suffix loop.

    :param repo_root: Absolute path of the repository's main work tree,
        e.g. ``"/Users/alice/myrepo"``.
    :returns: A path that does not yet exist, e.g.
        ``Path("/Users/alice/.omnigent/worktrees/myrepo/myrepo-1a2b3c4d-1709123456")``
    """
    base_dir = Path.home() / ".omnigent" / "worktrees"
    repo_name = _sanitize_repo_name(Path(repo_root).name)
    return base_dir / repo_name / f"{repo_name}-{uuid.uuid4().hex[:8]}-{int(time.time())}"


def _ensure_base_resolvable(repo_root: str, base_branch: str) -> None:
    """Make ``base_branch`` resolvable, fetching once if needed.

    If the base ref doesn't resolve locally (e.g. a remote-tracking
    branch not yet fetched), attempt a single ``git fetch`` and
    re-check. A fetch failure (offline) is not fatal on its own — the
    subsequent re-check produces the user-facing error.

    :param repo_root: Absolute repo work-tree root, e.g.
        ``"/Users/alice/myrepo"``.
    :param base_branch: Base ref the user requested, e.g. ``"main"``
        or ``"origin/main"``.
    :raises WorktreeError: If the base ref cannot be resolved even
        after a fetch attempt.
    """
    # --end-of-options forces git to treat the user-supplied base_branch as a
    # rev, never an option, so a value like "--exec-path" can't inject a git
    # flag (argv-only, no shell). Note: a bare "--" would not work here — git
    # rev-parse treats args after "--" as pathspecs, not revs.
    if (
        _run_git(
            ["rev-parse", "--verify", "--quiet", "--end-of-options", base_branch], cwd=repo_root
        ).returncode
        == 0
    ):
        return
    # Best-effort fetch from the default remote, then re-verify.
    _run_git(["fetch"], cwd=repo_root)
    if (
        _run_git(
            ["rev-parse", "--verify", "--quiet", "--end-of-options", base_branch], cwd=repo_root
        ).returncode
        != 0
    ):
        raise WorktreeError(f"base branch does not exist: {base_branch}")


def _local_branch_exists(repo_root: str, branch_name: str) -> bool:
    return (
        _run_git(
            ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch_name}"],
            cwd=repo_root,
        ).returncode
        == 0
    )


@dataclass
class CreatedWorktree:
    """Result of a successful worktree creation.

    :param worktree_path: Absolute path of the created worktree
        directory, e.g.
        ``"/Users/alice/myrepo-worktrees/feature-login"``.
    :param branch: The branch checked out in the worktree, e.g.
        ``"feature/login"``.
    """

    worktree_path: str
    branch: str


def _auto_cache_paths() -> tuple[Path, Path]:
    root = Path.home() / ".omnigent" / "worktrees"
    root.mkdir(parents=True, exist_ok=True)
    return root / ".auto-worktrees.json", root / ".auto-worktrees.lock"


def _read_folder_base_commit(worktree_path: str) -> str | None:
    """The managed folder record's base commit, or ``None``."""
    try:
        with _locked_auto_cache() as reg:
            folders = cast("dict[str, dict[str, object]]", reg["folders"])
            folder = folders.get(worktree_path)
            base = folder.get("base_commit") if isinstance(folder, dict) else None
            return base if isinstance(base, str) and base else None
    except WorktreeError:
        return None


def _lease_is_active(lease: object, folder_seq: object, now: int) -> bool:
    """Whether a lease record is a currently-valid claim on its folder."""
    if not isinstance(lease, dict):
        return False
    expires_at = lease.get("expires_at")
    return (
        isinstance(expires_at, int)
        and not isinstance(expires_at, bool)
        and expires_at > now
        and lease.get("seq") == folder_seq
    )


def _active_claim_sessions(
    leases: object, worktree_path: str, folder_seq: object, now: int
) -> list[str]:
    """Sessions holding a currently-valid claim on *worktree_path*."""
    if not isinstance(leases, dict):
        return []
    return [
        session
        for session, lease in leases.items()
        if isinstance(lease, dict)
        and lease.get("folder") == worktree_path
        and _lease_is_active(lease, folder_seq, now)
    ]


@contextmanager
def _locked_auto_cache() -> Generator[dict[str, object], None, None]:
    """Lock and persist the host-local managed-worktree registry."""
    registry_path, lock_path = _auto_cache_paths()
    with _AUTO_CACHE_PROCESS_LOCK, lock_path.open("a+", encoding="utf-8") as lock_file:
        deadline = time.monotonic() + 120.0
        if fcntl is not None:
            while True:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise WorktreeError(
                            "timed out waiting for the managed worktree lock"
                        ) from exc
                    time.sleep(0.1)
        elif msvcrt is not None:  # pragma: no cover - Windows.
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write("\0")
                lock_file.flush()
            lock_file.seek(0)
            while True:
                try:
                    msvcrt.locking(  # type: ignore[attr-defined]
                        lock_file.fileno(),
                        msvcrt.LK_NBLCK,  # type: ignore[attr-defined]
                        1,
                    )
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise WorktreeError(
                            "timed out waiting for the managed worktree lock"
                        ) from exc
                    time.sleep(0.1)
                try:
                    msvcrt.locking(  # type: ignore[attr-defined]
                        lock_file.fileno(),
                        msvcrt.LK_NBLCK,  # type: ignore[attr-defined]
                        1,
                    )
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise WorktreeError(
                            "timed out waiting for the managed worktree lock"
                        ) from exc
                    time.sleep(0.1)
        try:
            try:
                raw = json.loads(registry_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                raw = {}
            if not isinstance(raw, dict) or not raw:
                # Fresh host: seed the empty v2 registry.
                raw = {"version": _REGISTRY_VERSION, "folders": {}, "leases": {}}
            elif raw.get("version") != _REGISTRY_VERSION:
                raise WorktreeError(
                    "managed worktree registry has an unsupported schema "
                    f"(expected version {_REGISTRY_VERSION}); hand-migrate "
                    "the file to the v2 shape or delete it to start fresh"
                )
            if not isinstance(raw.get("folders"), dict):
                raw["folders"] = {}
            if not isinstance(raw.get("leases"), dict):
                raw["leases"] = {}
            entries = cast("dict[str, object]", raw)
            yield entries
            temp_path = registry_path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(entries, sort_keys=True), encoding="utf-8")
            os.replace(temp_path, registry_path)
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows.
                lock_file.seek(0)
                msvcrt.locking(  # type: ignore[attr-defined]
                    lock_file.fileno(),
                    msvcrt.LK_UNLCK,  # type: ignore[attr-defined]
                    1,
                )


def _worktree_is_clean(path: str) -> bool:
    try:
        result = _run_git(
            [
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            cwd=path,
        )
    except WorktreeError:
        # cwd doesn't exist (prunable worktree) — not clean
        return False
    if result.returncode != 0:
        return False
    return result.stdout.strip() == ""


def _reserve_auto_worktree(
    *,
    reg: dict[str, object],
    session_id: str,
    repo_root: str,
    base_commit: str,
    branch_name: str,
    reuse_existing_branch: bool,
    branch_in_refs: bool,
    reuse_path: str | None,
    lease_seconds: int,
    tried_paths: set[str],
    on_log: Callable[[str], None] | None,
) -> CreatedWorktree | tuple[str, str, int]:
    """Acquire phase A: decide and reserve, under the registry lock.

    Returns a :class:`CreatedWorktree` for the paths that need no git work
    (worktree adoption / own-folder reclaim). Otherwise reserves a folder —
    claim + fence + ``health="preparing"`` + ``reserved_at`` — and returns
    the plan ``(kind, path, reserved_at)`` for the unlocked git phase
    (``"switch"`` an existing pool folder, ``"add"`` a new folder for a
    branch already in refs, ``"create"`` a fresh branch + folder).
    """
    folders = cast("dict[str, dict[str, object]]", reg["folders"])
    leases = cast("dict[str, dict[str, object]]", reg["leases"])
    now = int(time.time())
    worktrees = {
        worktree.path: worktree for worktree in list_worktrees(repo_path=repo_root)
    }

    def _folder_seq(path: str) -> object:
        """Current fencing seq of a managed folder, or ``None``."""
        folder = folders.get(path)
        return folder.get("seq") if isinstance(folder, dict) else None

    def _contended(path: str) -> bool:
        """Whether another session holds a currently-valid claim."""
        return any(
            session != session_id
            for session in _active_claim_sessions(leases, path, _folder_seq(path), now)
        )

    def _claim_folder(
        path: str,
        *,
        branch: str | None,
        bump: bool,
    ) -> None:
        """Take or refresh the session's claim on a managed folder.

        ``bump`` fences the folder: every other session's claim becomes
        stale (seq mismatch) and its next dispatch relocates. Only the
        pool-adoption paths bump; co-use grants keep the current seq.
        """
        folder = folders.get(path)
        if not isinstance(folder, dict):
            folder = {
                "repo_root": repo_root,
                "created_at": now,
                "seq": 1,
                "generation": 1,
            }
            record = cast("dict[str, object]", folder)
            folders[path] = record
            new_seq = 1
        else:
            record = folder
            previous_seq = folder.get("seq")
            previous_seq = previous_seq if isinstance(previous_seq, int) else 0
            new_seq = previous_seq + 1 if bump else previous_seq
            record["seq"] = new_seq
        record["branch"] = branch
        record["base_commit"] = base_commit
        record["health"] = "ready"
        leases[session_id] = {
            "folder": path,
            "seq": new_seq,
            "expires_at": now + lease_seconds,
            "last_used_at": now,
            # Survives folder-record pruning: a fenced session must be
            # able to relocate even after its workspace dir is deleted.
            "repo_root": repo_root,
        }
        record["last_used_at"] = now

    def _adopt(worktree: WorktreeInfo, branch: str) -> CreatedWorktree | None:
        """Take over an existing worktree as-is, fencing other claims.

        Adopting as-is (skipping the clean check) is only safe when the
        folder is the session's own current-generation claim — its WIP.
        A folder whose current claim belongs to another generation may
        hold that session's uncommitted work; adopting it dirty would
        hand one session's WIP to another.
        """
        prior_lease = leases.get(session_id)
        own_current = (
            isinstance(prior_lease, dict)
            and prior_lease.get("folder") == worktree.path
            and prior_lease.get("seq") == _folder_seq(worktree.path)
        )
        if not own_current and not _worktree_is_clean(worktree.path):
            if on_log is not None:
                on_log(
                    f"Worktree {worktree.path} holds another generation's "
                    "uncommitted work; creating a fresh folder…"
                )
            return None
        _claim_folder(worktree.path, branch=branch, bump=True)
        if on_log is not None:
            on_log(f"Reacquired existing worktree {worktree.path}.")
        return CreatedWorktree(worktree_path=worktree.path, branch=branch)

    if reuse_existing_branch and reuse_path is not None:
        # Relocation: adopt the session's own worktree at its recorded
        # path when no other live session holds it — the worktree did
        # not change, so nothing is created or switched. The returned
        # branch is the worktree's current one (it may differ from the
        # recorded branch after a manual switch); the caller re-syncs
        # the session row to it.
        live = worktrees.get(reuse_path)
        if live is not None and not live.is_main and live.branch is not None:
            if _contended(reuse_path):
                if on_log is not None:
                    on_log(
                        f"Worktree {reuse_path} is held by another session;"
                        " relocating…"
                    )
            else:
                adopted = _adopt(live, live.branch)
                if adopted is not None:
                    return adopted
    if reuse_existing_branch:
        # Relocation fallback: adopt a live worktree that already has
        # the branch checked out (registry entry lost, but the branch
        # and its worktree survived). Same ownership guard applies.
        live = next(
            (
                wt
                for wt in worktrees.values()
                if wt.branch == branch_name and not wt.is_main
            ),
            None,
        )
        if live is not None and live.branch is not None:
            if not _contended(live.path):
                adopted = _adopt(live, live.branch)
                if adopted is not None:
                    return adopted

    # Reap stale reservations: a host crash mid-checkout leaves the folder
    # "preparing" with a live claim; free it after a generous TTL so it
    # returns to the pool. Finalize verifies ownership, so a resume after a
    # reap cannot double-switch a folder.
    for path, folder in list(folders.items()):
        if not isinstance(folder, dict) or folder.get("health") != "preparing":
            continue
        reserved_at = folder.get("reserved_at")
        if not isinstance(reserved_at, int) or isinstance(reserved_at, bool):
            continue
        if now - reserved_at < _RESERVED_STALE_S:
            continue
        folder_seq = folder.get("seq")
        for lease_session in list(leases):
            lease = leases[lease_session]
            if (
                isinstance(lease, dict)
                and lease.get("folder") == path
                and lease.get("seq") == folder_seq
            ):
                leases.pop(lease_session, None)
        folder.pop("reserved_at", None)

    candidates: list[tuple[str, dict[str, object]]] = []
    for path, folder in list(folders.items()):
        if not isinstance(path, str) or not isinstance(folder, dict):
            folders.pop(path, None)
            continue
        if folder.get("repo_root") != repo_root:
            continue
        worktree = worktrees.get(path)
        if worktree is None or worktree.is_main:
            # The worktree directory is gone — prune the record. Leases
            # pointing at it stay until their sessions relocate.
            folders.pop(path, None)
            continue
        if path in tried_paths:
            continue
        if _active_claim_sessions(leases, path, folder.get("seq"), now):
            # An unexpired claim holds this folder — not a reuse candidate.
            continue
        candidates.append((path, folder))

    def _last_used(candidate: tuple[str, dict[str, object]]) -> int:
        value = candidate[1].get("last_used_at")
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    for path, folder in sorted(candidates, key=_last_used):
        worktree = worktrees[path]
        prior_lease = leases.get(session_id)
        own_folder = (
            isinstance(prior_lease, dict)
            and prior_lease.get("folder") == path
            # Only the folder's CURRENT generation can be the session's
            # own WIP; a fenced lease means the WIP may belong to the
            # session that took the folder over since.
            and prior_lease.get("seq") == folder.get("seq")
        )
        if own_folder and worktree.branch == branch_name:
            # Reclaiming the session's own folder — its uncommitted work
            # is the session's WIP, so skip the clean check and reuse
            # it as-is.
            _claim_folder(path, branch=worktree.branch, bump=True)
            if on_log is not None:
                on_log(f"Reacquired existing worktree {path}.")
            return CreatedWorktree(worktree_path=path, branch=branch_name)
        if not _worktree_is_clean(path):
            folder["health"] = "dirty"
            continue
        previous_generation = folder.get("generation")
        folder["generation"] = (
            previous_generation
            if isinstance(previous_generation, int)
            and not isinstance(previous_generation, bool)
            else 0
        ) + 1
        # Reserve: claim + fence now, materialize with git below — outside
        # the lock. Finalize flips health back to "ready".
        _claim_folder(path, branch=branch_name, bump=True)
        folder["health"] = "preparing"
        folder["reserved_at"] = now
        folder["last_used_at"] = now
        if on_log is not None:
            on_log(f"Reusing managed worktree {path}…")
        return ("switch", path, now)
    # Reserve a brand-new folder; the directory itself is created by the
    # git work in the unlocked phase.
    new_path = _resolve_worktree_path(repo_root)
    new_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path = str(new_path)
    folders[plan_path] = {
        "repo_root": repo_root,
        "branch": branch_name,
        "base_commit": base_commit,
        "seq": 1,
        "generation": 1,
        "health": "preparing",
        "created_at": now,
        "last_used_at": now,
        "reserved_at": now,
    }
    leases[session_id] = {
        "folder": plan_path,
        "seq": 1,
        "expires_at": now + lease_seconds,
        "last_used_at": now,
        "repo_root": repo_root,
    }
    kind = "add" if reuse_existing_branch and branch_in_refs else "create"
    return (kind, plan_path, now)
def acquire_auto_worktree_streaming(
    *,
    repo_path: str,
    branch_name: str,
    session_id: str,
    base_branch: str | None = None,
    auto_fetch_base: bool = False,
    lease_seconds: int = _AUTO_LEASE_SECONDS,
    reuse_existing_branch: bool = False,
    reuse_path: str | None = None,
    on_log: Callable[[str], None] | None = None,
) -> CreatedWorktree:
    """Atomically reuse a clean managed worktree or create and lease one."""
    validate_branch_name(branch_name)
    repo_root = _main_work_tree(repo_path)
    if base_branch is not None and auto_fetch_base:
        # Always sync from remote so reused cached worktrees start from the
        # latest state of the base branch, not a stale local ref.  A fetch
        # failure (offline) is tolerated — _ensure_base_resolvable_streaming
        # still verifies the ref resolves and raises a proper error if not.
        if on_log is not None:
            on_log("Syncing from remote…")
        with suppress(WorktreeError):
            _run_git_streaming(
                ["fetch"],
                cwd=repo_root,
                on_log=on_log,
                label="git fetch failed",
            )
        _ensure_base_resolvable_streaming(repo_root, base_branch, on_log)
    branch_in_refs = False
    if reuse_existing_branch:
        # Relocation: the session's own branch is the base — the worktree
        # being re-acquired already sits on it. The branch may be missing
        # from the main repo's refs (renamed, deleted, or created on another
        # host); fall back to the caller's base ref, then HEAD, and create
        # the branch fresh instead of failing the relocation.
        base_ref = branch_name
        base_result = _run_git(
            ["rev-parse", "--verify", "--end-of-options", branch_name],
            cwd=repo_root,
        )
        if base_result.returncode == 0:
            branch_in_refs = True
        else:
            if base_branch is not None:
                if on_log is not None:
                    on_log(
                        f"Branch {branch_name!r} is not in this repo's refs; "
                        f"falling back to base {base_branch!r}…"
                    )
                _ensure_base_resolvable_streaming(repo_root, base_branch, on_log)
                base_result = _run_git(
                    ["rev-parse", "--verify", "--end-of-options", base_branch],
                    cwd=repo_root,
                )
            elif reuse_path is not None:
                # Relocation without an explicit base: recreate the missing
                # branch from the folder record's original base commit
                # (replaces the removed base_ref label) rather than an
                # unrelated current HEAD.
                recorded_base = _read_folder_base_commit(reuse_path)
                if recorded_base is not None:
                    if on_log is not None:
                        on_log(
                            f"Branch {branch_name!r} is not in this repo's refs; "
                            "falling back to the recorded base commit…"
                        )
                    base_result = _run_git(
                        ["rev-parse", "--verify", "--end-of-options", recorded_base],
                        cwd=repo_root,
                    )
            if base_result.returncode != 0:
                base_ref = "HEAD"
                base_result = _run_git(
                    ["rev-parse", "--verify", "--end-of-options", "HEAD"],
                    cwd=repo_root,
                )
    else:
        base_ref = base_branch or "HEAD"
        # Follow the selected linked worktree's HEAD rather than the main
        # work tree's branch when auto creation has no explicit base ref.
        base_cwd = repo_path if base_branch is None else repo_root
        base_result = _run_git(
            ["rev-parse", "--verify", "--end-of-options", base_ref],
            cwd=base_cwd,
        )
    if base_result.returncode != 0:
        raise WorktreeError(f"base branch does not exist: {base_ref}")
    base_commit = base_result.stdout.strip()

    tried_paths: set[str] = set()
    while True:
        # ---- Phase A: decide and reserve, under the registry lock. ----
        with _locked_auto_cache() as reg:
            outcome = _reserve_auto_worktree(
                reg=reg,
                session_id=session_id,
                repo_root=repo_root,
                base_commit=base_commit,
                branch_name=branch_name,
                reuse_existing_branch=reuse_existing_branch,
                branch_in_refs=branch_in_refs,
                reuse_path=reuse_path,
                lease_seconds=lease_seconds,
                tried_paths=tried_paths,
                on_log=on_log,
            )
        if isinstance(outcome, CreatedWorktree):
            return outcome
        plan_kind, plan_path, reserved_at_token = outcome

        # ---- Phase B: git work with the registry lock RELEASED, so lease
        # ops (renew/grant/release) never queue behind a checkout. ----
        created: CreatedWorktree | None = None
        failure: WorktreeError | None = None
        switch_rejected = False
        phase_b_ok = False
        try:
            if plan_kind == "switch":
                switch_args = (
                    ["switch", branch_name]
                    if reuse_existing_branch
                    else ["switch", "-c", branch_name, base_commit]
                )
                result = _run_git_streaming(
                    switch_args,
                    cwd=plan_path,
                    on_log=on_log,
                    label="git switch failed",
                )
                if result.returncode != 0:
                    switch_rejected = True
                else:
                    created = CreatedWorktree(
                        worktree_path=plan_path, branch=branch_name
                    )
            elif plan_kind == "add":
                result = _run_git_streaming(
                    ["worktree", "add", plan_path, branch_name],
                    cwd=repo_root,
                    on_log=on_log,
                    label="git worktree add failed",
                )
                if result.returncode != 0:
                    raise _git_error("git worktree add failed", result)
                created = CreatedWorktree(
                    worktree_path=plan_path, branch=branch_name
                )
            else:
                if (
                    reuse_existing_branch
                    and not branch_in_refs
                    and on_log is not None
                ):
                    on_log(f"Recreating branch {branch_name!r} from base…")
                created = create_worktree_streaming(
                    repo_path=repo_root,
                    branch_name=branch_name,
                    base_branch=base_commit,
                    auto_fetch_base=False,
                    on_log=on_log,
                )
            phase_b_ok = created is not None
        except WorktreeError as exc:
            failure = exc
        finally:
            # ---- Phase C: finalize the reservation, or roll it back. ----
            now = int(time.time())
            with _locked_auto_cache() as reg:
                folders = cast("dict[str, dict[str, object]]", reg["folders"])
                leases = cast("dict[str, dict[str, object]]", reg["leases"])
                folder = folders.get(plan_path)
                if phase_b_ok and failure is None and not switch_rejected:
                    if not isinstance(folder, dict):
                        folders[plan_path] = {
                            "repo_root": repo_root,
                            "branch": (
                                created.branch if created else branch_name
                            ),
                            "base_commit": base_commit,
                            "seq": 1,
                            "generation": 1,
                            "health": "ready",
                            "created_at": now,
                            "last_used_at": now,
                        }
                    elif folder.get("reserved_at") == reserved_at_token:
                        folder["health"] = "ready"
                        folder["branch"] = (
                            created.branch if created else branch_name
                        )
                        folder["last_used_at"] = now
                    else:
                        # The reservation was reaped (host suspended past the
                        # TTL) and someone else took the folder — never clobber
                        # another session's claim on it.
                        failure = WorktreeError(
                            "reserved worktree was reclaimed before it was"
                            " ready; retry the operation"
                        )
                else:
                    if (
                        isinstance(folder, dict)
                        and folder.get("reserved_at") == reserved_at_token
                    ):
                        folder.pop("reserved_at", None)
                        if plan_kind == "switch":
                            folder["health"] = "quarantined"
                        else:
                            folders.pop(plan_path, None)
                    lease = leases.get(session_id)
                    if (
                        isinstance(lease, dict)
                        and lease.get("folder") == plan_path
                        and (
                            folder is None
                            or lease.get("seq") == folder.get("seq")
                        )
                    ):
                        leases.pop(session_id, None)
        if failure is not None and plan_kind != "switch":
            raise failure
        if failure is not None or switch_rejected:
            # A rejected switch falls through to the next pool candidate; a
            # failed add/create raised above.
            tried_paths.add(plan_path)
            continue
        assert created is not None
        return created


def grant_auto_worktree_lease(
    *,
    worktree_path: str,
    session_id: str,
    lease_seconds: int = _AUTO_LEASE_SECONDS,
) -> bool:
    """Grant the session's lease on a managed folder at its current seq.

    Used when a session binds to a folder that is already managed — its own
    creation, a relocation rebind, or deliberate co-use of an in-use
    folder. Grants never bump the seq, so co-users never invalidate each
    other. ``False`` when the folder is not managed.
    """
    now = int(time.time())
    with _locked_auto_cache() as reg:
        folders = cast("dict[str, dict[str, object]]", reg["folders"])
        folder = folders.get(worktree_path)
        if not isinstance(folder, dict):
            return False
        seq = folder.get("seq")
        seq = seq if isinstance(seq, int) else 0
        leases = cast("dict[str, dict[str, object]]", reg["leases"])
        leases[session_id] = {
            "folder": worktree_path,
            "seq": seq,
            "expires_at": now + lease_seconds,
            "last_used_at": now,
            "repo_root": folder.get("repo_root"),
        }
        folder["last_used_at"] = now
        return True


def renew_auto_worktree_lease(
    *,
    worktree_path: str,
    session_id: str,
    lease_seconds: int = _AUTO_LEASE_SECONDS,
) -> dict[str, object]:
    """Validate and extend the session's lease.

    :returns: ``{valid, managed, repo_root}``. ``managed`` says whether the
        session participates in the managed-worktree model at all: it holds
        a lease on this folder, or the folder is a registered managed
        folder. A lease-less session on a managed folder (fork /
        existing-worktree binding before its first claim, or an expired
        lease whose folder nobody took over) earns its claim here — grant
        at the folder's current seq, no bump.

        ``valid=False`` with ``managed=True`` means the folder was
        reassigned: the folder's seq moved past the lease's seq (fencing),
        or the folder record is gone. The session must relocate;
        ``repo_root`` (persisted on the lease) is the repo to rebuild from
        when the workspace directory no longer exists.
    """
    now = int(time.time())
    with _locked_auto_cache() as reg:
        folders = cast("dict[str, dict[str, object]]", reg["folders"])
        leases = cast("dict[str, dict[str, object]]", reg["leases"])
        folder = folders.get(worktree_path)
        folder_managed = isinstance(folder, dict)
        lease = leases.get(session_id)
        lease_on_folder = isinstance(lease, dict) and lease.get("folder") == worktree_path
        if not folder_managed and not lease_on_folder:
            return {"valid": False, "managed": False, "repo_root": None}
        if not isinstance(folder, dict):
            # The folder record was pruned (worktree directory deleted) —
            # the session's workspace is gone; relocate from the lease's
            # persisted repo root.
            return {
                "valid": False,
                "managed": True,
                "repo_root": lease.get("repo_root") if isinstance(lease, dict) else None,
            }
        seq = folder.get("seq")
        seq = seq if isinstance(seq, int) else 0
        folder_repo_root = folder.get("repo_root")
        if not lease_on_folder:
            # First claim by this session on a managed folder.
            leases[session_id] = {
                "folder": worktree_path,
                "seq": seq,
                "expires_at": now + lease_seconds,
                "last_used_at": now,
                "repo_root": folder_repo_root,
            }
            folder["last_used_at"] = now
            return {"valid": True, "managed": True, "repo_root": folder_repo_root}
        assert isinstance(lease, dict)
        if lease.get("seq") != seq:
            # Fenced: another session took the folder over. Relocate.
            fenced_repo_root = lease.get("repo_root") or folder_repo_root
            return {"valid": False, "managed": True, "repo_root": fenced_repo_root}
        lease["expires_at"] = now + lease_seconds
        lease["last_used_at"] = now
        if not lease.get("repo_root"):
            lease["repo_root"] = folder_repo_root
        folder["last_used_at"] = now
        return {"valid": True, "managed": True, "repo_root": lease.get("repo_root")}


def check_auto_worktree_lease(*, worktree_path: str, session_id: str) -> dict[str, object]:
    """Read-only probe: is the folder managed, and does the session hold a
    valid claim on it?

    Unlike :func:`grant_auto_worktree_lease` / :func:`renew_auto_worktree_lease`
    this never creates or extends a claim — the launch path's boundary
    carve-out uses it to confirm the requesting session already owns a
    claim on the folder (e.g. from creating it) before admitting the
    folder past the agent's ``os_env.cwd`` boundary.

    :returns: ``{managed, valid}`` — ``managed`` says the folder is a
        registered managed worktree; ``valid`` says the session holds an
        unexpired lease on it at the folder's current seq.
    """
    now = int(time.time())
    with _locked_auto_cache() as reg:
        folders = cast("dict[str, dict[str, object]]", reg["folders"])
        leases = cast("dict[str, dict[str, object]]", reg["leases"])
        folder = folders.get(worktree_path)
        managed = isinstance(folder, dict)
        lease = leases.get(session_id)
        if not managed or not isinstance(lease, dict):
            return {"managed": managed, "valid": False}
        if lease.get("folder") != worktree_path:
            return {"managed": True, "valid": False}
        seq = folder.get("seq") if isinstance(folder, dict) else None
        lease_seq = lease.get("seq")
        expires_at = lease.get("expires_at")
        # Bool guards match _lease_is_active: isinstance(int) accepts
        # bool, and a corrupted seq/expires_at must not validate.
        valid = (
            isinstance(lease_seq, int)
            and not isinstance(lease_seq, bool)
            and lease_seq == (seq if isinstance(seq, int) and not isinstance(seq, bool) else -1)
            and isinstance(expires_at, int)
            and not isinstance(expires_at, bool)
            and expires_at > now
        )
        return {"managed": True, "valid": valid}


def release_auto_worktree_lease(*, session_id: str) -> dict[str, object]:
    """Drop the session's lease. Returns the folder's post-release state.

    :returns: ``{released, folder_path, folder_managed, folder_free}`` —
        ``folder_free`` is whether no unexpired claims remain, i.e. the
        folder is back in the reuse pool.
    """
    with _locked_auto_cache() as reg:
        folders = cast("dict[str, dict[str, object]]", reg["folders"])
        leases = cast("dict[str, dict[str, object]]", reg["leases"])
        lease = leases.pop(session_id, None)
        folder_path: str | None = None
        folder_managed = False
        folder_free = True
        if isinstance(lease, dict) and isinstance(lease.get("folder"), str):
            folder_path = cast("str", lease["folder"])
            folder = folders.get(folder_path)
            folder_managed = isinstance(folder, dict)
            folder_seq = folder.get("seq") if isinstance(folder, dict) else None
            folder_free = not _active_claim_sessions(
                leases, folder_path, folder_seq, int(time.time())
            )
        return {
            "released": isinstance(lease, dict),
            "folder_path": folder_path,
            "folder_managed": folder_managed,
            "folder_free": folder_free,
        }


def folder_has_active_claims(*, worktree_path: str) -> bool:
    """Whether any unexpired claim currently holds *worktree_path*."""
    now = int(time.time())
    with _locked_auto_cache() as reg:
        folders = cast("dict[str, dict[str, object]]", reg["folders"])
        folder = folders.get(worktree_path)
        folder_seq = folder.get("seq") if isinstance(folder, dict) else None
        return bool(_active_claim_sessions(reg["leases"], worktree_path, folder_seq, now))


def create_worktree(
    *,
    repo_path: str,
    branch_name: str,
    base_branch: str | None = None,
    existing_branch: bool = False,
) -> CreatedWorktree:
    """Create a git worktree with a new — or existing — branch checked out.

    Resolves the repo root, picks a collision-free Omnigent directory,
    and runs ``git worktree add -b`` (fetching once if ``base_branch``
    isn't locally resolvable). With ``existing_branch`` the branch must
    already exist and not be checked out in any live worktree; stale
    registrations (a worktree whose directory was deleted from disk)
    are pruned first, and the branch is checked out without ``-b`` —
    the recreate path for a deleted worktree.

    :param repo_path: Absolute path inside the source repo — the
        directory the user picked, e.g. ``"/Users/alice/myrepo"``.
    :param branch_name: New branch to create and check out, e.g.
        ``"feature/login"``. With ``existing_branch``, the pre-existing
        branch to check out instead.
    :param base_branch: Optional base ref, e.g. ``"main"``. ``None``
        branches from the repo's current ``HEAD``. Invalid with
        ``existing_branch`` (an existing branch has no base to fork).
    :param existing_branch: When ``True``, check out the pre-existing
        ``branch_name`` into a fresh worktree instead of creating a new
        branch.
    :returns: The created worktree's path and branch.
    :raises WorktreeError: If the branch name is invalid, the path is
        not a git repo, the base ref can't be resolved, or
        ``git worktree add`` fails (e.g. the branch already exists in
        create mode, is missing or still checked out in
        existing-branch mode).
    """
    validate_branch_name(branch_name)
    if existing_branch and base_branch is not None:
        raise WorktreeError("base_branch cannot be set when checking out an existing branch")
    # Always create the worktree off the MAIN work tree, even when
    # ``repo_path`` is itself a linked worktree (e.g. the fork-resume
    # picker prefilled a worktree as the source). Otherwise the new
    # Git operations should target the shared main checkout even when the
    # selected path is itself a linked worktree.
    repo_root = _main_work_tree(repo_path)
    if existing_branch:
        if not _local_branch_exists(repo_root, branch_name):
            raise WorktreeError(
                f"branch {branch_name!r} does not exist; cannot recreate its worktree"
            )
        # A deleted worktree directory leaves a stale registration that
        # keeps the branch "in use" — prune it so the add below can
        # check the branch out again. Prune only drops registrations
        # whose directories are gone; live worktrees are untouched.
        _run_git(["worktree", "prune"], cwd=repo_root)
        live = next(
            (wt for wt in list_worktrees(repo_path=repo_root) if wt.branch == branch_name),
            None,
        )
        if live is not None:
            raise WorktreeError(
                f"branch {branch_name!r} is already checked out at {live.path}; "
                "remove that worktree first or choose a different branch name"
            )
    # Friendly pre-check before git's raw "branch already exists" error.
    # We don't reuse the existing worktree: two sessions sharing one
    # working tree would clobber each other (designs/SESSION_GIT_WORKTREE.md).
    elif _local_branch_exists(repo_root, branch_name):
        raise WorktreeError(
            f"a branch named {branch_name!r} already exists; choose a different branch name"
        )
    if base_branch is not None:
        # Fetch-and-retry keeps a base ref that only exists on the remote
        # (or a renamed default branch) working instead of failing the create.
        _ensure_base_resolvable(repo_root, base_branch)
    worktree_path = _resolve_worktree_path(repo_root)
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    if existing_branch:
        # --end-of-options: treat the branch as a rev, never a git flag
        # (argv-only, no shell). No ``-b`` — the branch already exists.
        add_args = ["worktree", "add", str(worktree_path), "--end-of-options", branch_name]
    else:
        add_args = ["worktree", "add", "-b", branch_name, str(worktree_path)]
        if base_branch is not None:
            # --end-of-options: treat base_branch as a rev, never a git flag,
            # so a user-supplied value starting with '-' can't inject an
            # option.
            add_args += ["--end-of-options", base_branch]
    result = _run_git(add_args, cwd=repo_root)
    if result.returncode != 0:
        raise _git_error("git worktree add failed", result)
    return CreatedWorktree(worktree_path=str(worktree_path), branch=branch_name)


def _run_git_streaming(
    args: list[str],
    *,
    cwd: str,
    on_log: Callable[[str], None] | None,
    label: str,
) -> subprocess.CompletedProcess[str]:
    """Run git with Popen, streaming stdout+stderr line-by-line.

    Falls back to :func:`_run_git` (captured, no streaming) when
    ``on_log`` is ``None`` — the non-streaming create path keeps its
    original behavior.

    :param args: Git argv *after* ``git``.
    :param cwd: Working directory to run git in.
    :param on_log: Callback for each output line, or ``None`` to
        suppress streaming.
    :param label: Short label for the error message on failure,
        e.g. ``"git worktree add failed"``.
    :returns: The completed process (stdout/stderr captured even when
        streaming, so the error path can include detail).
    :raises WorktreeError: If git is not installed.
    """
    if on_log is None:
        return _run_git(args, cwd=cwd)
    argv = ["git", *args]
    stdout_parts: list[str] = []
    git_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    def _emit(text: str) -> None:
        if text:
            stdout_parts.append(f"{text}\n")
            on_log(text)

    try:
        if os.name == "posix":
            import pty

            master_fd, slave_fd = pty.openpty()
            try:
                try:
                    proc = subprocess.Popen(
                        argv,
                        cwd=cwd,
                        env=git_env,
                        stdin=subprocess.DEVNULL,
                        stdout=slave_fd,
                        stderr=slave_fd,
                        close_fds=True,
                    )
                except Exception:
                    os.close(master_fd)
                    raise
            finally:
                os.close(slave_fd)

            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            pending = ""
            try:
                while True:
                    readable, _, _ = select.select([master_fd], [], [], 0.1)
                    if not readable:
                        # A helper inherited the PTY after git exited. Waiting
                        # for that unrelated process to close its copy would
                        # leave a completed worktree stuck forever.
                        if proc.poll() is not None:
                            break
                        continue
                    try:
                        chunk = os.read(master_fd, 4096)
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            break
                        raise
                    if not chunk:
                        break
                    pending += decoder.decode(chunk)
                    parts = re.split(r"\r\n|\r|\n", pending)
                    pending = parts.pop()
                    for part in parts:
                        _emit(part)
                pending += decoder.decode(b"", final=True)
                _emit(pending)
            finally:
                os.close(master_fd)
            proc.wait(timeout=_GIT_TIMEOUT_S)
        else:
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                env=git_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                _emit(line.rstrip("\r\n"))
            proc.wait(timeout=_GIT_TIMEOUT_S)
    except FileNotFoundError as exc:
        if not Path(cwd).is_dir():
            raise WorktreeError(f"worktree directory does not exist: {cwd}") from exc
        raise WorktreeError("git is not installed on the host") from exc

    output = "".join(stdout_parts)
    result = subprocess.CompletedProcess(
        args=argv,
        returncode=proc.returncode,
        stdout=output,
        stderr=output if proc.returncode else "",
    )
    if result.returncode != 0:
        raise _git_error(label, result)
    return result


def create_worktree_streaming(
    *,
    repo_path: str,
    branch_name: str,
    base_branch: str | None = None,
    existing_branch: bool = False,
    auto_fetch_base: bool = False,
    on_log: Callable[[str], None] | None = None,
) -> CreatedWorktree:
    """Create a git worktree, streaming git output line-by-line.

    Same logic as :func:`create_worktree`, but the two potentially slow
    steps — ``git fetch`` (when the base ref isn't locally resolvable)
    and ``git worktree add`` — use :func:`_run_git_streaming` so each
    stdout/stderr line is relayed to ``on_log`` in real time. Quick
    validation steps produce a single summary log line each.

    :param repo_path: Absolute path inside the source repo.
    :param branch_name: New branch to create and check out. With
        ``existing_branch``, the pre-existing branch to check out instead.
    :param base_branch: Optional base ref, e.g. ``"main"``. Invalid with
        ``existing_branch`` (an existing branch has no base to fork).
    :param existing_branch: When ``True``, check out the pre-existing
        ``branch_name`` into a fresh worktree (the deleted-worktree
        recreate path) instead of creating a new branch.
    :param auto_fetch_base: Verify, fetch, and retry an unavailable base
        before creating the worktree. Defaults off.
    :param on_log: Callback for each output line, or ``None`` to
        suppress streaming (non-streaming create path).
    :returns: The created worktree's path and branch.
    :raises WorktreeError: If any git step fails.
    """
    validate_branch_name(branch_name)
    if existing_branch and base_branch is not None:
        raise WorktreeError("base_branch cannot be set when checking out an existing branch")
    if on_log is not None:
        on_log(f"Resolving repository root for {repo_path}…")
    repo_root = _main_work_tree(repo_path)
    if on_log is not None:
        on_log(f"Repository root: {repo_root}")
    if existing_branch:
        if not _local_branch_exists(repo_root, branch_name):
            raise WorktreeError(
                f"branch {branch_name!r} does not exist; cannot recreate its worktree"
            )
        # A deleted worktree directory leaves a stale registration that
        # keeps the branch "in use" — prune it so the add below can
        # check the branch out again. Prune only drops registrations
        # whose directories are gone; live worktrees are untouched.
        _run_git(["worktree", "prune"], cwd=repo_root)
        live = next(
            (wt for wt in list_worktrees(repo_path=repo_root) if wt.branch == branch_name),
            None,
        )
        if live is not None:
            raise WorktreeError(
                f"branch {branch_name!r} is already checked out at {live.path}; "
                "remove that worktree first or choose a different branch name"
            )
    elif _local_branch_exists(repo_root, branch_name):
        raise WorktreeError(
            f"a branch named {branch_name!r} already exists; choose a different branch name"
        )
    if base_branch is not None and auto_fetch_base:
        if on_log is not None:
            on_log(f"Resolving base branch '{base_branch}'…")
        _ensure_base_resolvable_streaming(repo_root, base_branch, on_log)
    worktree_path = _resolve_worktree_path(repo_root)
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    if existing_branch:
        # --end-of-options: treat the branch as a rev, never a git flag
        # (argv-only, no shell). No ``-b`` — the branch already exists.
        add_args = ["worktree", "add", str(worktree_path), "--end-of-options", branch_name]
    else:
        add_args = ["worktree", "add", "-b", branch_name, str(worktree_path)]
        if base_branch is not None:
            add_args += ["--end-of-options", base_branch]
    if on_log is not None:
        on_log(f"$ {shlex.join(['git', '-C', repo_root, *add_args])}")
    _run_git_streaming(
        add_args,
        cwd=repo_root,
        on_log=on_log,
        label="git worktree add failed",
    )
    if on_log is not None:
        on_log(f"Worktree created: {worktree_path}")
    return CreatedWorktree(worktree_path=str(worktree_path), branch=branch_name)


def _ensure_base_resolvable_streaming(
    repo_root: str,
    base_branch: str,
    on_log: Callable[[str], None] | None,
) -> None:
    """Streaming variant of :func:`_ensure_base_resolvable`.

    Streams the ``git fetch`` output when a fetch is needed.

    :param repo_root: Absolute repo work-tree root.
    :param base_branch: Base ref the user requested.
    :param on_log: Callback for each output line.
    :raises WorktreeError: If the base ref cannot be resolved.
    """
    if (
        _run_git(
            ["rev-parse", "--verify", "--quiet", "--end-of-options", base_branch],
            cwd=repo_root,
        ).returncode
        == 0
    ):
        return
    if on_log is not None:
        on_log("Fetching from remote…")
    _run_git_streaming(
        ["fetch"],
        cwd=repo_root,
        on_log=on_log,
        label="git fetch failed",
    )
    if (
        _run_git(
            ["rev-parse", "--verify", "--quiet", "--end-of-options", base_branch],
            cwd=repo_root,
        ).returncode
        != 0
    ):
        raise WorktreeError(f"base branch does not exist: {base_branch}")


def _main_repo_for_worktree(worktree_path: str) -> str:
    """Find the main repository work tree for a linked worktree.

    Uses ``git rev-parse --git-common-dir`` (which points at the
    shared ``.git`` of the main work tree) and returns that directory's
    parent. Run from inside the worktree so the relative result
    resolves correctly.

    :param worktree_path: Absolute path of a linked worktree, e.g.
        ``"/Users/alice/.omnigent/worktrees/feature-login"``.
    :returns: Absolute path of the main repo work tree, e.g.
        ``"/Users/alice/myrepo"``.
    :raises WorktreeError: If ``worktree_path`` is missing or not part
        of a git repository.
    """
    if not Path(worktree_path).exists():
        raise WorktreeError(f"worktree path does not exist: {worktree_path}")
    result = _run_git(["rev-parse", "--git-common-dir"], cwd=worktree_path)
    if result.returncode != 0:
        raise WorktreeError(f"not a git worktree: {worktree_path}")
    common_dir = Path(result.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = (Path(worktree_path) / common_dir).resolve()
    return str(common_dir.parent)


def _orphaned_worktree_main_repo(worktree_path: str) -> str | None:
    """Recover the main repo from a linked worktree's stale ``.git`` file."""
    git_file = Path(worktree_path) / ".git"
    if not git_file.is_file() or git_file.is_symlink():
        return None
    try:
        marker = git_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    prefix = "gitdir:"
    if not marker.lower().startswith(prefix):
        return None
    metadata = Path(marker[len(prefix) :].strip())
    if not metadata.is_absolute():
        metadata = (git_file.parent / metadata).resolve()
    if metadata.exists():
        return None
    common_dir = metadata.parent.parent
    if common_dir.name != ".git" or not common_dir.is_dir():
        return None
    return str(common_dir.parent)


def remove_worktree(
    *,
    worktree_path: str,
    branch: str | None = None,
    delete_branch: bool = False,
) -> None:
    """Remove a git worktree and optionally delete its branch.

    Removes the directory with ``--force``, then (if requested) deletes
    the branch — in that order, since git refuses to delete a branch
    still checked out in a linked worktree. ``git worktree remove``
    refuses to remove the main work tree.

    :param worktree_path: Absolute path of the worktree to remove,
        e.g. ``"/Users/alice/.omnigent/worktrees/feature-login"``.
    :param branch: Branch to delete when ``delete_branch`` is
        ``True``, e.g. ``"feature/login"``. ``None`` skips branch
        deletion.
    :param delete_branch: When ``True``, run ``git branch -D`` on
        ``branch`` after removing the worktree directory.
    :raises WorktreeError: If the worktree path is missing/invalid, the
        folder still holds another session's active lease, or a git
        command fails.
    """
    # Registry-aware guard: a managed folder with another session's
    # unexpired claim must not be removed out from under it (closes the
    # release→remove window against a concurrent acquire re-claiming the
    # freed folder).
    if folder_has_active_claims(worktree_path=worktree_path):
        raise WorktreeError(
            "managed worktree is still leased by another session; "
            "release its lease before removing"
        )
    try:
        main_repo = _main_repo_for_worktree(worktree_path)
    except WorktreeError:
        main_repo = _orphaned_worktree_main_repo(worktree_path)
        if main_repo is None:
            raise
        try:
            shutil.rmtree(worktree_path)
        except OSError as exc:
            raise WorktreeError(f"failed to remove orphaned worktree: {exc}") from exc
    else:
        remove_result = _run_git(
            ["worktree", "remove", "--force", worktree_path],
            cwd=main_repo,
        )
        if remove_result.returncode != 0:
            raise _git_error("git worktree remove failed", remove_result)
    if delete_branch and branch is not None:
        branch_exists = _run_git(
            ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=main_repo,
        )
        if branch_exists.returncode != 0:
            return
        branch_result = _run_git(["branch", "-D", branch], cwd=main_repo)
        if branch_result.returncode != 0:
            raise _git_error("git branch -D failed", branch_result)
