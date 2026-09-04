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
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

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
_AUTO_CACHE_PROCESS_LOCK = threading.RLock()

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
    ``~/.omnigent/worktrees/<repo-name>/<repo-name>-<timestamp>``, using
    nanosecond precision for collision-free uniqueness without a suffix
    loop.

    :param repo_root: Absolute path of the repository's main work tree,
        e.g. ``"/Users/alice/myrepo"``.
    :returns: A path that does not yet exist, e.g.
        ``Path("/Users/alice/.omnigent/worktrees/myrepo/myrepo-1709123456789012345")
    """
    base_dir = Path.home() / ".omnigent" / "worktrees"
    repo_name = _sanitize_repo_name(Path(repo_root).name)
    return base_dir / repo_name / f"{repo_name}-{time.time_ns()}"


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


@contextmanager
def _locked_auto_cache() -> Generator[dict[str, object], None, None]:
    """Lock and persist the host-local managed-worktree registry."""
    registry_path, lock_path = _auto_cache_paths()
    with _AUTO_CACHE_PROCESS_LOCK, lock_path.open("a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        elif msvcrt is not None:  # pragma: no cover - Windows.
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write("\0")
                lock_file.flush()
            lock_file.seek(0)
            deadline = time.monotonic() + 120.0
            while True:
                try:
                    msvcrt.locking(
                        lock_file.fileno(),
                        msvcrt.LK_NBLCK,
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
            entries: dict[str, object] = raw if isinstance(raw, dict) else {}
            yield entries
            temp_path = registry_path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(entries, sort_keys=True), encoding="utf-8")
            os.replace(temp_path, registry_path)
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows.
                lock_file.seek(0)
                msvcrt.locking(
                    lock_file.fileno(),
                    msvcrt.LK_UNLCK,
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


def acquire_auto_worktree_streaming(
    *,
    repo_path: str,
    branch_name: str,
    lease_owner: str,
    base_branch: str | None = None,
    auto_fetch_base: bool = False,
    lease_seconds: int = _AUTO_LEASE_SECONDS,
    reuse_existing_branch: bool = False,
    on_log: Callable[[str], None] | None = None,
    on_reclaim: Callable[[str, str], bool] | None = None,
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
    base_ref = branch_name if reuse_existing_branch else (base_branch or "HEAD")
    # Follow the selected linked worktree's HEAD rather than the main work
    # tree's branch when auto creation has no explicit base ref.
    base_cwd = repo_path if base_branch is None and not reuse_existing_branch else repo_root
    base_result = _run_git(
        ["rev-parse", "--verify", "--end-of-options", base_ref],
        cwd=base_cwd,
    )
    if base_result.returncode != 0:
        raise WorktreeError(f"base branch does not exist: {base_ref}")
    base_commit = base_result.stdout.strip()
    now = int(time.time())

    with _locked_auto_cache() as raw_entries:
        if not isinstance(raw_entries, dict):  # pragma: no cover - context normalizes this
            raise WorktreeError("managed worktree registry is invalid")
        worktrees = {worktree.path: worktree for worktree in list_worktrees(repo_path=repo_root)}
        candidates: list[tuple[str, dict[str, object]]] = []
        for path, raw_entry in list(raw_entries.items()):
            if not isinstance(path, str) or not isinstance(raw_entry, dict):
                raw_entries.pop(path, None)
                continue
            if raw_entry.get("repo_root") != repo_root:
                continue
            worktree = worktrees.get(path)
            if worktree is None or worktree.is_main:
                raw_entries.pop(path, None)
                continue
            expires_at = raw_entry.get("lease_expires_at")
            owner = raw_entry.get("lease_owner")
            if owner == lease_owner or not isinstance(expires_at, int) or expires_at <= now:
                candidates.append((path, raw_entry))

        def _last_used(candidate: tuple[str, dict[str, object]]) -> int:
            value = candidate[1].get("last_used_at")
            return value if isinstance(value, int) and not isinstance(value, bool) else 0

        for path, entry in sorted(candidates, key=_last_used):
            same_owner = entry.get("lease_owner") == lease_owner
            worktree = worktrees[path]
            if same_owner and worktree.branch == branch_name:
                entry.update(
                    {
                        "lease_expires_at": now + lease_seconds,
                        "last_used_at": now,
                        "health": "ready",
                    }
                )
                if on_log is not None:
                    on_log(f"Reacquired existing worktree {path}.")
                return CreatedWorktree(worktree_path=path, branch=branch_name)
            previous_owner = entry.get("lease_owner")
            if (
                isinstance(previous_owner, str)
                and previous_owner != lease_owner
                and on_reclaim is not None
                and not on_reclaim(previous_owner, path)
            ):
                continue
            if not _worktree_is_clean(path):
                entry["health"] = "dirty"
                entry["lease_expires_at"] = None
                continue
            previous_generation = entry.get("generation")
            generation = (
                previous_generation
                if isinstance(previous_generation, int)
                and not isinstance(previous_generation, bool)
                else 0
            ) + 1
            entry.update(
                {
                    "lease_owner": lease_owner,
                    "lease_expires_at": now + lease_seconds,
                    "generation": generation,
                    "health": "preparing",
                    "last_used_at": now,
                }
            )
            if on_log is not None:
                on_log(f"Reusing managed worktree {path}…")
            try:
                switch_args = (
                    ["switch", branch_name]
                    if reuse_existing_branch
                    else ["switch", "-c", branch_name, base_commit]
                )
                result = _run_git_streaming(
                    switch_args,
                    cwd=path,
                    on_log=on_log,
                    label="git switch failed",
                )
            except WorktreeError:
                entry["health"] = "quarantined"
                entry["lease_owner"] = None
                entry["lease_expires_at"] = None
                continue
            if result.returncode != 0:
                entry["health"] = "quarantined"
                entry["lease_owner"] = None
                entry["lease_expires_at"] = None
                continue
            entry.update(
                {
                    "branch": branch_name,
                    "base_commit": base_commit,
                    "health": "ready",
                }
            )
            return CreatedWorktree(worktree_path=path, branch=branch_name)

        if reuse_existing_branch:
            worktree_path = _resolve_worktree_path(repo_root)
            worktree_path.parent.mkdir(parents=True, exist_ok=True)
            result = _run_git_streaming(
                ["worktree", "add", str(worktree_path), branch_name],
                cwd=repo_root,
                on_log=on_log,
                label="git worktree add failed",
            )
            if result.returncode != 0:
                raise _git_error("git worktree add failed", result)
            created = CreatedWorktree(worktree_path=str(worktree_path), branch=branch_name)
        else:
            created = create_worktree_streaming(
                repo_path=repo_root,
                branch_name=branch_name,
                base_branch=base_commit,
                auto_fetch_base=False,
                on_log=on_log,
            )
        raw_entries[created.worktree_path] = {
            "repo_root": repo_root,
            "branch": created.branch,
            "base_commit": base_commit,
            "lease_owner": lease_owner,
            "lease_expires_at": now + lease_seconds,
            "generation": 1,
            "health": "ready",
            "created_at": now,
            "last_used_at": now,
        }
        return created


def renew_auto_worktree_lease(
    *,
    worktree_path: str,
    lease_owner: str,
    lease_seconds: int = _AUTO_LEASE_SECONDS,
    release: bool = False,
) -> bool:
    """Extend a managed lease only when the caller still owns it."""
    now = int(time.time())
    with _locked_auto_cache() as entries:
        entry = entries.get(worktree_path)
        if not isinstance(entry, dict) or entry.get("lease_owner") != lease_owner:
            return False
        entry["lease_expires_at"] = 0 if release else now + lease_seconds
        if release:
            entry["lease_owner"] = None
        entry["last_used_at"] = now
        return True


def create_worktree(
    *,
    repo_path: str,
    branch_name: str,
    base_branch: str | None = None,
    auto_fetch_base: bool = False,
) -> CreatedWorktree:
    """Create a git worktree with a new branch checked out.

    Resolves the repo root, picks a collision-free Omnigent directory,
    and runs ``git worktree add -b`` (fetching once if ``base_branch``
    isn't locally resolvable).

    :param repo_path: Absolute path inside the source repo — the
        directory the user picked, e.g. ``"/Users/alice/myrepo"``.
    :param branch_name: New branch to create and check out, e.g.
        ``"feature/login"``.
    :param base_branch: Optional base ref, e.g. ``"main"``. ``None``
        branches from the repo's current ``HEAD``.
    :param auto_fetch_base: Verify, fetch, and retry an unavailable base
        before creating the worktree. Defaults off.
    :returns: The created worktree's path and branch.
    :raises WorktreeError: If the branch name is invalid, the path is
        not a git repo, the base ref can't be resolved, or
        ``git worktree add`` fails (e.g. the branch already exists).
    """
    validate_branch_name(branch_name)
    # Always create the worktree off the MAIN work tree, even when
    # ``repo_path`` is itself a linked worktree (e.g. the fork-resume
    # picker prefilled a worktree as the source). Otherwise the new
    # Git operations should target the shared main checkout even when the
    # selected path is itself a linked worktree.
    repo_root = _main_work_tree(repo_path)
    if base_branch is not None and auto_fetch_base:
        _ensure_base_resolvable(repo_root, base_branch)
    worktree_path = _resolve_worktree_path(repo_root)
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    add_args = ["worktree", "add", "-b", branch_name, str(worktree_path)]
    if base_branch is not None:
        # --end-of-options: treat base_branch as a rev, never a git flag, so a
        # user-supplied value starting with '-' can't inject an option.
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
    :param branch_name: New branch to create and check out.
    :param base_branch: Optional base ref, e.g. ``"main"``.
    :param auto_fetch_base: Verify, fetch, and retry an unavailable base
        before creating the worktree. Defaults off.
    :param on_log: Callback for each output line, or ``None`` to
        suppress streaming (non-streaming create path).
    :returns: The created worktree's path and branch.
    :raises WorktreeError: If any git step fails.
    """
    validate_branch_name(branch_name)
    if on_log is not None:
        on_log(f"Resolving repository root for {repo_path}…")
    repo_root = _main_work_tree(repo_path)
    if on_log is not None:
        on_log(f"Repository root: {repo_root}")
    if base_branch is not None and auto_fetch_base:
        if on_log is not None:
            on_log(f"Resolving base branch '{base_branch}'…")
        _ensure_base_resolvable_streaming(repo_root, base_branch, on_log)
    worktree_path = _resolve_worktree_path(repo_root)
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
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
    :raises WorktreeError: If the worktree path is missing/invalid, or
        a git command fails.
    """
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
