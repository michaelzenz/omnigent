"""Tests for host-side git worktree operations.

Exercises ``omnigent.host.git_worktree`` against real ``git`` in a
temp repository — the operations run actual ``git worktree add`` /
``remove`` / ``branch -D`` so a regression in argv construction, repo-
root resolution, or removal ordering fails loud here.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from omnigent.host.git_worktree import (
    CreatedWorktree,
    WorktreeError,
    acquire_auto_worktree_streaming,
    create_worktree,
    folder_has_active_claims,
    grant_auto_worktree_lease,
    list_worktrees,
    release_auto_worktree_lease,
    remove_worktree,
    renew_auto_worktree_lease,
    validate_branch_name,
)

# Deterministic identity + config so the tests don't depend on the
# developer's global git config (user.name / init.defaultBranch).
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def _git(repo: Path, *args: str) -> None:
    """Run a git command in ``repo``, raising on failure.

    :param repo: Repository directory to run in.
    :param args: Git arguments after ``git``, e.g. ``("add", ".")``.
    """
    import os

    subprocess.run(
        ["git", *args],
        cwd=repo,
        env={**os.environ, **_GIT_ENV},
        check=True,
        capture_output=True,
    )


def _current_branch(path: Path) -> str:
    """Return the checked-out branch name at ``path``.

    :param path: A work tree (main or linked worktree) directory.
    :returns: Branch name, e.g. ``"feature/login"``.
    """
    import os

    return subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=path,
        env={**os.environ, **_GIT_ENV},
        capture_output=True,
        text=True,
    ).stdout.strip()


def _rev_parse(path: Path, ref: str = "HEAD") -> str:
    """Return the commit sha that ``ref`` resolves to at ``path``.

    :param path: A work tree directory.
    :param ref: Ref to resolve, e.g. ``"HEAD"`` or ``"develop"``.
    :returns: The 40-char commit sha.
    """
    import os

    return subprocess.run(
        ["git", "rev-parse", ref],
        cwd=path,
        env={**os.environ, **_GIT_ENV},
        capture_output=True,
        text=True,
    ).stdout.strip()


def _branch_exists(repo: Path, branch: str) -> bool:
    """Return whether ``branch`` exists in ``repo``.

    :param repo: Repository directory.
    :param branch: Branch name to check, e.g. ``"feature/login"``.
    :returns: ``True`` if the local branch exists.
    """
    import os

    out = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=repo,
        env={**os.environ, **_GIT_ENV},
        capture_output=True,
        text=True,
    ).stdout.strip()
    return out != ""


def _worktree_count(repo: Path) -> int:
    """Return how many worktrees are registered for ``repo``.

    :param repo: Repository directory.
    :returns: Worktree count, where ``1`` means only the main work
        tree exists (no linked worktree was added).
    """
    import os

    out = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo,
        env={**os.environ, **_GIT_ENV},
        capture_output=True,
        text=True,
    ).stdout
    # --porcelain emits one "worktree <path>" line per worktree.
    return out.count("worktree ")


@pytest.fixture()
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Create a one-commit git repo and yield its resolved root.

    :returns: Iterator yielding the repo root path (realpath, so it
        matches what ``git rev-parse --show-toplevel`` returns).
    """
    # Resolve so comparisons match git's realpath output (macOS
    # /tmp -> /private/tmp).
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = (tmp_path / "myrepo").resolve()
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hi")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    yield repo


def test_create_worktree_uses_omnigent_worktree_root(git_repo: Path) -> None:
    """A new worktree lands under ``~/.omnigent/worktrees/<repo-name>/``."""
    created = create_worktree(repo_path=str(git_repo), branch_name="feature/login")
    expected_parent = Path.home() / ".omnigent" / "worktrees" / "myrepo"
    # Path proves the grouped layout: <repo-name>/<repo-name>-<uuid>-<timestamp>.
    assert Path(created.worktree_path).parent == expected_parent
    assert Path(created.worktree_path).name.startswith("myrepo-")
    name = Path(created.worktree_path).name
    assert re.fullmatch(r"myrepo-[0-9a-f]{8}-\d{10}", name), name
    assert Path(created.worktree_path).is_dir()
    # The branch is actually checked out in the worktree (not just the dir made).
    assert _current_branch(Path(created.worktree_path)) == "feature/login"
    assert isinstance(created, CreatedWorktree)


def _wipe_auto_registry() -> None:
    """Empty the host-local managed-worktree registry file."""
    _registry_path().write_text("{}")


def _registry_path() -> Path:
    """Return the host-local managed-worktree registry file path."""
    path = Path.home() / ".omnigent" / "worktrees" / ".auto-worktrees.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _expire_all_claims(worktree_path: str) -> None:
    """Expire every lease pointing at *worktree_path* (registry surgery)."""
    reg = json.loads(_registry_path().read_text())
    for lease in reg["leases"].values():
        if lease.get("folder") == worktree_path:
            lease["expires_at"] = 0
    _registry_path().write_text(json.dumps(reg))


def _active_sessions(worktree_path: str) -> list[str]:
    """Sessions whose lease currently validates against the folder."""
    reg = json.loads(_registry_path().read_text())
    folder = reg["folders"][worktree_path]
    now = int(time.time())
    return [
        session
        for session, lease in reg["leases"].items()
        if lease.get("folder") == worktree_path
        and lease.get("seq") == folder.get("seq")
        and isinstance(lease.get("expires_at"), int)
        and lease["expires_at"] > now
    ]


def test_auto_worktree_relocation_adopts_same_path_worktree(git_repo: Path) -> None:
    """Relocation re-acquires the session's own worktree, branch renamed.

    The recorded branch is gone from the main repo's refs (renamed inside
    the worktree) and the registry entry was lost — the worktree itself is
    unchanged, so relocation must adopt it as-is (keeping its current
    branch) instead of failing with "base branch does not exist".
    """
    first = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/orig-aaaaaa",
        session_id="session-1",
    )
    worktree_path = Path(first.worktree_path)
    # The user switches branches inside the worktree; the old ref vanishes.
    _git(worktree_path, "switch", "-q", "-c", "agent/switched-bbbbbb")
    _git(worktree_path, "branch", "-D", "agent/orig-aaaaaa")
    assert not _branch_exists(git_repo, "agent/orig-aaaaaa")
    # Host daemon reset: the managed-worktree registry entry is gone.
    _wipe_auto_registry()
    count_before = _worktree_count(git_repo)

    relocated = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/orig-aaaaaa",
        session_id="session-1",
        reuse_existing_branch=True,
        reuse_path=str(worktree_path),
        base_branch="main",
    )

    assert relocated.worktree_path == first.worktree_path
    assert relocated.branch == "agent/switched-bbbbbb"
    assert _current_branch(worktree_path) == "agent/switched-bbbbbb"
    assert _worktree_count(git_repo) == count_before


def test_auto_worktree_relocation_recreates_missing_branch_from_base(
    git_repo: Path,
) -> None:
    """Relocation creates the worktree instead of failing on a gone branch.

    The session branch is missing from the host repo's refs and no worktree
    holds it; with a caller-provided base ref the relocation must recreate
    the branch off that base rather than raising "base branch does not
    exist".
    """
    count_before = _worktree_count(git_repo)
    base_commit = subprocess.run(
        ["git", "rev-parse", "main"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    relocated = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/gone-cccccc",
        session_id="session-1",
        reuse_existing_branch=True,
        base_branch="main",
    )

    assert _worktree_count(git_repo) == count_before + 1
    assert _branch_exists(git_repo, "agent/gone-cccccc")
    assert _current_branch(Path(relocated.worktree_path)) == "agent/gone-cccccc"
    # ``worktree add -b`` creates the branch AT the base commit.
    tip = subprocess.run(
        ["git", "rev-parse", "agent/gone-cccccc"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert tip == base_commit


def test_auto_worktree_relocation_skips_path_held_by_another_session(
    git_repo: Path,
) -> None:
    """Relocation never steals a worktree another live session holds."""
    other = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/other-dddddd",
        session_id="session-2",
    )

    relocated = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/mine-eeeeee",
        session_id="session-1",
        reuse_existing_branch=True,
        reuse_path=str(other.worktree_path),
        base_branch="main",
    )

    assert relocated.worktree_path != other.worktree_path
    assert _current_branch(Path(other.worktree_path)) == "agent/other-dddddd"


def test_auto_worktree_reuses_only_expired_clean_entry(git_repo: Path) -> None:
    first = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/first-aaaaaa",
        session_id="session-1",
    )
    second = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/second-bbbbbb",
        session_id="session-2",
    )
    assert second.worktree_path != first.worktree_path

    _expire_all_claims(first.worktree_path)

    reused = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/third-cccccc",
        session_id="session-3",
    )
    assert reused.worktree_path == first.worktree_path
    assert _current_branch(Path(reused.worktree_path)) == "agent/third-cccccc"
    assert _branch_exists(git_repo, "agent/first-aaaaaa")


def test_auto_worktree_quarantines_expired_dirty_entry(git_repo: Path) -> None:
    first = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/dirty-aaaaaa",
        session_id="session-1",
    )
    dirty_file = Path(first.worktree_path) / "local.txt"
    dirty_file.write_text("keep me")

    _expire_all_claims(first.worktree_path)

    acquired = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/new-bbbbbb",
        session_id="session-2",
    )
    assert acquired.worktree_path != first.worktree_path
    assert dirty_file.read_text() == "keep me"
    result = renew_auto_worktree_lease(
        worktree_path=first.worktree_path,
        session_id="session-1",
    )
    # The folder was never reassigned (nobody adopted the dirty entry), so
    # the original session's renewal re-grants in place.
    assert result["valid"] is True and result["managed"] is True


# ── Fencing-lease model: N claims per folder, seq-gated validity ────────
#
# Every host session on a managed git folder holds its own lease; a lease
# is valid while its seq matches the folder's AND it is unexpired. Pool
# adoption requires ALL claims on the folder to be expired, and bumps the
# folder's seq — instantly fencing every other claim.


def test_active_claim_blocks_pool_reuse(git_repo: Path) -> None:
    """A folder with an unexpired claim is never handed to a new session."""
    holder = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/held-aaaaaa",
        session_id="session-1",
    )
    assert folder_has_active_claims(worktree_path=holder.worktree_path)

    taker = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/taker-bbbbbb",
        session_id="session-2",
    )

    assert taker.worktree_path != holder.worktree_path
    assert _current_branch(Path(holder.worktree_path)) == "agent/held-aaaaaa"


def test_expired_claims_free_folder_for_reuse_and_fence_holders(git_repo: Path) -> None:
    """All-expired claims let a new session take over and fence the old."""
    first = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/old-aaaaaa",
        session_id="session-1",
    )
    _expire_all_claims(first.worktree_path)
    assert not folder_has_active_claims(worktree_path=first.worktree_path)

    taker = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/new-bbbbbb",
        session_id="session-2",
    )

    assert taker.worktree_path == first.worktree_path
    assert _current_branch(Path(first.worktree_path)) == "agent/new-bbbbbb"
    # The takeover bumped the folder's seq: session-1's claim is fenced.
    reg = json.loads(_registry_path().read_text())
    assert reg["leases"]["session-1"]["seq"] != reg["folders"][first.worktree_path]["seq"]
    assert reg["leases"]["session-2"]["seq"] == reg["folders"][first.worktree_path]["seq"]


def test_renew_extends_valid_lease_and_regrants_expired_untaken(git_repo: Path) -> None:
    """Renew extends a valid lease; an expired-but-untaken lease re-grants."""
    first = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/renew-aaaaaa",
        session_id="session-1",
    )
    result = renew_auto_worktree_lease(worktree_path=first.worktree_path, session_id="session-1")
    assert result["valid"] is True and result["managed"] is True

    # Expire the lease; nobody took the folder, so renewal re-grants in
    # place instead of forcing a relocation.
    _expire_all_claims(first.worktree_path)
    reg = json.loads(_registry_path().read_text())
    stale = reg["leases"]["session-1"]
    stale["expires_at"] = int(time.time()) + 86_400
    _registry_path().write_text(json.dumps(reg))
    result = renew_auto_worktree_lease(worktree_path=first.worktree_path, session_id="session-1")
    assert result["valid"] is True and result["managed"] is True
    assert folder_has_active_claims(worktree_path=first.worktree_path)


def test_renew_after_takeover_reports_fenced(git_repo: Path) -> None:
    """A fenced lease (folder reassigned) must relocate, not re-grant."""
    first = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/fenced-aaaaaa",
        session_id="session-1",
    )
    _expire_all_claims(first.worktree_path)
    acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/taker-bbbbbb",
        session_id="session-2",
    )

    result = renew_auto_worktree_lease(worktree_path=first.worktree_path, session_id="session-1")

    assert result["valid"] is False and result["managed"] is True
    # The lease carries the repo root so relocation works even after the
    # workspace directory is deleted.
    assert result["repo_root"] == str(git_repo)


def test_renew_plain_folder_is_not_managed(git_repo: Path) -> None:
    """A workspace outside the managed registry gets (False, False)."""
    result = renew_auto_worktree_lease(worktree_path=str(git_repo), session_id="session-1")
    assert result["valid"] is False and result["managed"] is False
    assert result["repo_root"] is None


def test_leaseless_session_on_managed_folder_earns_claim(git_repo: Path) -> None:
    """A co-user without a claim (fork / bind) earns it on first renew."""
    holder = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/shared-aaaaaa",
        session_id="session-1",
    )

    result = renew_auto_worktree_lease(worktree_path=holder.worktree_path, session_id="session-2")

    assert result["valid"] is True and result["managed"] is True
    assert sorted(_active_sessions(holder.worktree_path)) == ["session-1", "session-2"]


def test_grant_lease_on_managed_folder_couses(git_repo: Path) -> None:
    """An explicit bind to an in-use folder grants a co-claim, no bump."""
    holder = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/couse-aaaaaa",
        session_id="session-1",
    )
    seq_before = json.loads(_registry_path().read_text())["folders"][holder.worktree_path]["seq"]

    assert grant_auto_worktree_lease(worktree_path=holder.worktree_path, session_id="session-2")

    assert sorted(_active_sessions(holder.worktree_path)) == ["session-1", "session-2"]
    seq_after = json.loads(_registry_path().read_text())["folders"][holder.worktree_path]["seq"]
    assert seq_after == seq_before
    # Grant on a non-managed folder fails.
    assert not grant_auto_worktree_lease(worktree_path=str(git_repo), session_id="session-3")


def test_branch_switch_does_not_affect_leases(git_repo: Path) -> None:
    """Branch is orthogonal to lease validity.

    A session switching the folder's branch (a plain git operation inside
    the worktree) neither invalidates its own lease nor any co-user's:
    validity is seq + expiry only. The folder record's branch is passive
    metadata describing what is checked out.
    """
    first = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/switch-aaaaaa",
        session_id="session-1",
    )
    worktree_path = Path(first.worktree_path)

    # A session switches the folder to another branch (no acquire involved).
    _git(worktree_path, "switch", "-q", "-c", "agent/switch-bbbbbb")

    # The lease survives the switch, and a relocation re-adopt keeps the
    # folder as-is, returning the worktree's CURRENT branch (the session
    # row re-syncs to it) instead of failing on the renamed branch.
    result = renew_auto_worktree_lease(worktree_path=first.worktree_path, session_id="session-1")
    assert result["valid"] is True and result["managed"] is True
    relocated = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/switch-aaaaaa",
        session_id="session-1",
        reuse_existing_branch=True,
        reuse_path=str(worktree_path),
        base_branch="main",
    )
    assert relocated.worktree_path == first.worktree_path
    assert relocated.branch == "agent/switch-bbbbbb"

    # A co-user earns its claim on the switched folder; both leases stay
    # valid across a further switch, with no seq bump (nothing fenced).
    renew_auto_worktree_lease(worktree_path=first.worktree_path, session_id="session-2")
    assert sorted(_active_sessions(first.worktree_path)) == ["session-1", "session-2"]
    _git(worktree_path, "switch", "-q", "-c", "agent/switch-cccccc")
    assert sorted(_active_sessions(first.worktree_path)) == ["session-1", "session-2"]
    reg = json.loads(_registry_path().read_text())
    folder_seq = reg["folders"][first.worktree_path]["seq"]
    assert reg["leases"]["session-1"]["seq"] == folder_seq
    assert reg["leases"]["session-2"]["seq"] == folder_seq
    # Renewals after the switch: both still valid.
    for session in ("session-1", "session-2"):
        result = renew_auto_worktree_lease(worktree_path=first.worktree_path, session_id=session)
        assert result["valid"] is True and result["managed"] is True


def test_renew_after_folder_prune_reports_fenced_with_repo_root(git_repo: Path) -> None:
    """A pruned folder record (dir deleted) fences the lease, with repo root.

    The session's workspace directory is gone; renewal must report
    ``valid=False, managed=True`` plus the persisted repo root so the
    server can relocate from the repo instead of the deleted path.
    """
    first = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/pruned-aaaaaa",
        session_id="session-1",
    )
    reg = json.loads(_registry_path().read_text())
    del reg["folders"][first.worktree_path]
    _registry_path().write_text(json.dumps(reg))

    result = renew_auto_worktree_lease(worktree_path=first.worktree_path, session_id="session-1")

    assert result["valid"] is False and result["managed"] is True
    assert result["repo_root"] == str(git_repo)


def test_dirty_reclaim_requires_current_generation(git_repo: Path) -> None:
    """A fenced session may not reclaim a folder holding another's WIP.

    S is fenced off folder F; T takes F over and leaves uncommitted work.
    When S's next acquire runs, F is a free candidate again — but its WIP
    belongs to T's generation, so S must not skip the clean check; the
    dirty folder is skipped and S gets a fresh one.
    """
    first = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/dirtygen-aaaaaa",
        session_id="session-1",
    )
    _expire_all_claims(first.worktree_path)
    taker = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/dirtygen-bbbbbb",
        session_id="session-2",
    )
    assert taker.worktree_path == first.worktree_path
    # T leaves uncommitted work, then its claim lapses.
    (Path(first.worktree_path) / "t-wip.txt").write_text("taker WIP")
    _expire_all_claims(first.worktree_path)

    reacquired = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/dirtygen-aaaaaa",
        session_id="session-1",
        reuse_existing_branch=True,
    )

    # F is dirty with T's WIP: S cannot reclaim it as its own; it gets a
    # fresh folder and T's work stays untouched.
    assert reacquired.worktree_path != first.worktree_path
    assert (Path(first.worktree_path) / "t-wip.txt").read_text() == "taker WIP"


def test_dirty_own_current_generation_reclaims_in_place(git_repo: Path) -> None:
    """The current-gen holder reclaims its own dirty folder as-is."""
    first = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/wip-aaaaaa",
        session_id="session-1",
    )
    (Path(first.worktree_path) / "wip.txt").write_text("my WIP")

    # Relocation-style re-acquire of the session's own recorded path: the
    # claim is the folder's current generation, so the dirty WIP is the
    # session's own and the adopt skips the clean check.
    reacquired = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/wip-aaaaaa",
        session_id="session-1",
        reuse_existing_branch=True,
        reuse_path=str(first.worktree_path),
    )

    assert reacquired.worktree_path == first.worktree_path
    assert (Path(first.worktree_path) / "wip.txt").read_text() == "my WIP"


def test_release_drops_claim_and_reports_folder_free(git_repo: Path) -> None:
    """Release removes the claim; folder_free flips when the last goes."""
    first = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/rel-aaaaaa",
        session_id="session-1",
    )
    acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/couse-cccccc",
        session_id="session-2",
    )
    grant_auto_worktree_lease(worktree_path=first.worktree_path, session_id="session-2")

    result = release_auto_worktree_lease(session_id="session-1")
    assert result["released"] is True
    assert result["folder_managed"] is True
    assert result["folder_free"] is False  # session-2 still holds a claim

    result = release_auto_worktree_lease(session_id="session-2")
    assert result["folder_free"] is True
    assert not folder_has_active_claims(worktree_path=first.worktree_path)
    # Releasing with no lease is a harmless no-op.
    result = release_auto_worktree_lease(session_id="session-1")
    assert result["released"] is False


def test_session_holds_single_lease_across_rebind(git_repo: Path) -> None:
    """A session's 1:1 lease: rebinding to a new folder drops the old."""
    acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/rebind-aaaaaa",
        session_id="session-1",
    )
    second = acquire_auto_worktree_streaming(
        repo_path=str(git_repo),
        branch_name="agent/rebind-bbbbbb",
        session_id="session-1",
    )

    reg = json.loads(_registry_path().read_text())
    assert list(reg["leases"].keys()) == ["session-1"]
    assert reg["leases"]["session-1"]["folder"] == second.worktree_path


def test_v1_registry_fails_loud_without_migration(git_repo: Path) -> None:
    """A v1 file is refused until migrated — no fallback reading."""
    legacy = {
        "/wt/a": {"repo_root": str(git_repo), "lease_owner": "session-1"},
    }
    _registry_path().write_text(json.dumps(legacy))

    with pytest.raises(WorktreeError, match="unsupported schema"):
        acquire_auto_worktree_streaming(
            repo_path=str(git_repo),
            branch_name="agent/x-aaaaaa",
            session_id="session-1",
        )


def test_auto_worktree_syncs_from_remote_before_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reusing a cached worktree with ``auto_fetch_base`` fetches first.

    A repo with a bare remote is created, a managed worktree is cached,
    then a new commit is pushed to the remote.  The next acquire must
    fetch before resolving the base so the reused worktree starts from
    the latest remote commit, not the stale local ``origin/main``.
    """
    import os

    monkeypatch.setenv("HOME", str(tmp_path))

    # Bare remote + clone so ``origin/main`` is a remote-tracking ref.
    remote = (tmp_path / "remote.git").resolve()
    remote.mkdir()
    _git(remote, "init", "-q", "--bare", "-b", "main")
    local = (tmp_path / "myrepo").resolve()
    subprocess.run(
        ["git", "clone", "-q", str(remote), str(local)],
        env={**os.environ, **_GIT_ENV},
        check=True,
    )
    (local / "README.md").write_text("v1")
    _git(local, "add", ".")
    _git(local, "commit", "-q", "-m", "v1")
    _git(local, "push", "-q", "origin", "main")
    v1_commit = _rev_parse(local, "origin/main")

    logs: list[str] = []
    first = acquire_auto_worktree_streaming(
        repo_path=str(local),
        branch_name="agent/first-aaaaaa",
        session_id="session-1",
        base_branch="origin/main",
        auto_fetch_base=True,
        on_log=logs.append,
    )

    # Expire the lease so the worktree becomes a reuse candidate.
    _expire_all_claims(first.worktree_path)

    # Push a new commit to the remote.
    (local / "README.md").write_text("v2")
    _git(local, "add", ".")
    _git(local, "commit", "-q", "-m", "v2")
    _git(local, "push", "-q", "origin", "main")
    v2_commit = _rev_parse(local, "origin/main")
    assert v1_commit != v2_commit, "remote should have moved"

    logs.clear()
    reused = acquire_auto_worktree_streaming(
        repo_path=str(local),
        branch_name="agent/second-bbbbbb",
        session_id="session-2",
        base_branch="origin/main",
        auto_fetch_base=True,
        on_log=logs.append,
    )
    assert reused.worktree_path == first.worktree_path
    # The reused worktree must be based on the fetched v2 commit.
    assert _rev_parse(Path(reused.worktree_path)) == v2_commit
    assert any("Syncing from remote" in line for line in logs)


def test_auto_worktree_without_base_uses_linked_worktree_head(git_repo: Path) -> None:
    """A fork from a linked worktree follows that worktree's commit."""
    source = create_worktree(repo_path=str(git_repo), branch_name="feature/source")
    source_path = Path(source.worktree_path)
    (source_path / "source.txt").write_text("source commit")
    _git(source_path, "add", ".")
    _git(source_path, "commit", "-q", "-m", "source")

    created = acquire_auto_worktree_streaming(
        repo_path=str(source_path),
        branch_name="agent/fork-aaaaaa",
        session_id="session-fork",
    )

    assert _rev_parse(Path(created.worktree_path)) == _rev_parse(source_path)
    assert _rev_parse(Path(created.worktree_path)) != _rev_parse(git_repo)


def test_create_worktree_resolves_repo_root_from_subdir(git_repo: Path) -> None:
    """Picking a subdir still creates under Omnigent's managed root, grouped by repo name."""
    sub = git_repo / "src"
    sub.mkdir()
    created = create_worktree(repo_path=str(sub), branch_name="wip")
    expected_parent = Path.home() / ".omnigent" / "worktrees" / "myrepo"
    assert Path(created.worktree_path).parent == expected_parent
    assert Path(created.worktree_path).name.startswith("myrepo-")


def test_create_worktree_from_linked_worktree_uses_managed_root(git_repo: Path) -> None:
    """Creating from a linked worktree still uses the managed root, grouped by repo name."""
    # First worktree, created off the main repo.
    first = create_worktree(repo_path=str(git_repo), branch_name="feature/a")
    first_path = Path(first.worktree_path)
    expected_repo_dir = Path.home() / ".omnigent" / "worktrees" / "myrepo"
    assert first_path.parent == expected_repo_dir
    assert first_path.name.startswith("myrepo-")

    # Second worktree, requested from INSIDE the first (linked) worktree.
    second = create_worktree(repo_path=str(first_path), branch_name="feature/b")

    assert Path(second.worktree_path).parent == expected_repo_dir
    assert Path(second.worktree_path).name.startswith("myrepo-")
    assert Path(second.worktree_path).is_dir()
    assert _current_branch(Path(second.worktree_path)) == "feature/b"


def test_create_worktree_from_base_branch(git_repo: Path) -> None:
    """A worktree branches from the explicit base ref's tip, not HEAD."""
    # Advance develop with its own commit so it differs from main —
    # otherwise the test would pass even if base_branch were ignored
    # (both would resolve to the same single commit).
    _git(git_repo, "checkout", "-q", "-b", "develop")
    (git_repo / "dev.txt").write_text("dev-only")
    _git(git_repo, "add", ".")
    _git(git_repo, "commit", "-q", "-m", "dev commit")
    _git(git_repo, "checkout", "-q", "main")

    created = create_worktree(
        repo_path=str(git_repo), branch_name="from-develop", base_branch="develop"
    )
    assert _current_branch(Path(created.worktree_path)) == "from-develop"
    # Points at develop's tip, not main's — proves base_branch routed
    # the new branch to develop rather than falling back to HEAD.
    assert _rev_parse(Path(created.worktree_path)) == _rev_parse(git_repo, "develop")
    assert _rev_parse(Path(created.worktree_path)) != _rev_parse(git_repo, "main")


def test_create_worktree_unknown_base_branch_fails(git_repo: Path) -> None:
    """An unresolvable base ref fails loud (after the best-effort fetch)."""
    with pytest.raises(WorktreeError) as exc:
        create_worktree(repo_path=str(git_repo), branch_name="x", base_branch="nope-not-a-branch")
    # Proves _ensure_base_resolvable rejects rather than silently
    # branching from HEAD when the requested base is missing.
    assert "base branch does not exist" in exc.value.message


@pytest.mark.parametrize("option_like", ["-f", "--exec-path"])
def test_create_worktree_option_like_base_branch_not_executed(
    git_repo: Path, option_like: str
) -> None:
    """A base_branch that looks like a git flag is rejected, never executed.

    ``base_branch`` is user-supplied and reaches ``git rev-parse`` and
    ``git worktree add`` argv. An option-like value (e.g. ``"-f"``, which
    is ``git worktree add``'s ``--force``) must be treated as an
    unresolvable rev, not parsed as a flag. This guards the end-to-end
    security property at the public API: the ref-resolution pre-check and
    the ``--end-of-options`` argv terminators together keep such a value
    from creating a worktree. A regression that let ``"-f"`` through as a
    flag would build a worktree from the wrong base (and force-create it)
    instead of failing — so the assertion below would see a linked
    worktree appear.
    """
    with pytest.raises(WorktreeError):
        create_worktree(repo_path=str(git_repo), branch_name="from-flag", base_branch=option_like)
    # Still only the main work tree — no linked worktree was added, proving
    # git treated the value as a (rejected) rev rather than a flag that
    # would have run `worktree add`. If `-f` were parsed as --force, the
    # count would be 2.
    assert _worktree_count(git_repo) == 1


def test_create_worktree_duplicate_branch_fails(git_repo: Path) -> None:
    """Creating two worktrees for the same branch name fails loud with the friendly error."""
    create_worktree(repo_path=str(git_repo), branch_name="dup")
    with pytest.raises(WorktreeError) as exc:
        create_worktree(repo_path=str(git_repo), branch_name="dup")
    # The pre-check catches the existing branch before git's raw error;
    # we must NOT silently reuse the existing worktree.
    assert "already exists" in exc.value.message


def test_create_worktree_existing_branch_no_worktree_fails(git_repo: Path) -> None:
    """A branch that exists WITHOUT a worktree is still rejected by the pre-check.

    Proves the pre-check keys off branch existence, not directory
    occupancy — creating a worktree for a plain pre-existing branch
    would otherwise hit git's raw error.
    """
    _git(git_repo, "branch", "preexisting")
    with pytest.raises(WorktreeError) as exc:
        create_worktree(repo_path=str(git_repo), branch_name="preexisting")
    assert "already exists" in exc.value.message
    assert "preexisting" in exc.value.message


def test_create_worktree_existing_branch_recreates_after_dir_deleted(git_repo: Path) -> None:
    """A branch whose worktree directory was deleted can be checked back out.

    Simulates the deleted-worktree fork: the directory is rm'd from disk
    (leaving a stale registration), then ``existing_branch=True`` prunes
    the stale entry and adds a fresh worktree for the same branch.
    """
    import shutil

    created = create_worktree(repo_path=str(git_repo), branch_name="fix-1")
    shutil.rmtree(created.worktree_path)
    recreated = create_worktree(repo_path=str(git_repo), branch_name="fix-1", existing_branch=True)
    assert recreated.branch == "fix-1"
    assert Path(recreated.worktree_path).is_dir()
    assert _current_branch(Path(recreated.worktree_path)) == "fix-1"


def test_create_worktree_existing_branch_missing_branch_fails(git_repo: Path) -> None:
    """``existing_branch=True`` for a branch that doesn't exist fails loud."""
    with pytest.raises(WorktreeError) as exc:
        create_worktree(repo_path=str(git_repo), branch_name="ghost", existing_branch=True)
    assert "does not exist" in exc.value.message
    assert _worktree_count(git_repo) == 1


def test_create_worktree_existing_branch_live_worktree_fails(git_repo: Path) -> None:
    """``existing_branch=True`` refuses a branch checked out in a LIVE worktree.

    Two sessions must never share one working tree; only a stale (deleted-
    from-disk) registration is pruned, a live one aborts.
    """
    create_worktree(repo_path=str(git_repo), branch_name="busy")
    with pytest.raises(WorktreeError) as exc:
        create_worktree(repo_path=str(git_repo), branch_name="busy", existing_branch=True)
    assert "already checked out" in exc.value.message
    assert _worktree_count(git_repo) == 2


def test_create_worktree_existing_branch_rejects_base_branch(git_repo: Path) -> None:
    """``existing_branch`` + ``base_branch`` is contradictory and rejected."""
    _git(git_repo, "branch", "have")
    with pytest.raises(WorktreeError) as exc:
        create_worktree(
            repo_path=str(git_repo),
            branch_name="have",
            base_branch="main",
            existing_branch=True,
        )
    assert "base_branch" in exc.value.message


def test_create_worktree_non_repo_fails(tmp_path: Path) -> None:
    """A directory that isn't a git repo is rejected."""
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(WorktreeError) as exc:
        create_worktree(repo_path=str(plain), branch_name="x")
    assert "not a git repository" in exc.value.message


def test_remove_worktree_deletes_dir_and_branch(git_repo: Path) -> None:
    """``delete_branch=True`` removes the directory AND the branch."""
    created = create_worktree(repo_path=str(git_repo), branch_name="feature/login")
    remove_worktree(
        worktree_path=created.worktree_path, branch="feature/login", delete_branch=True
    )
    # Directory gone (git worktree remove --force ran)...
    assert not Path(created.worktree_path).exists()
    # ...and the branch deleted (git branch -D ran, after the worktree
    # was removed — git would refuse otherwise).
    assert not _branch_exists(git_repo, "feature/login")


def test_remove_worktree_keeps_branch_when_flag_false(git_repo: Path) -> None:
    """``delete_branch=False`` removes the directory but keeps the branch."""
    created = create_worktree(repo_path=str(git_repo), branch_name="feature/keep")
    remove_worktree(
        worktree_path=created.worktree_path, branch="feature/keep", delete_branch=False
    )
    assert not Path(created.worktree_path).exists()
    # Branch survives — only the checkout directory was removed.
    assert _branch_exists(git_repo, "feature/keep")


def test_remove_worktree_recovers_orphaned_directory(git_repo: Path) -> None:
    """A stale gitdir pointer can still be removed with its branch."""
    created = create_worktree(repo_path=str(git_repo), branch_name="feature/orphan")
    worktree_path = Path(created.worktree_path)
    marker = (worktree_path / ".git").read_text(encoding="utf-8").strip()
    metadata = Path(marker.removeprefix("gitdir:").strip())
    shutil.rmtree(metadata)

    remove_worktree(
        worktree_path=created.worktree_path,
        branch="feature/orphan",
        delete_branch=True,
    )

    assert not worktree_path.exists()
    assert not _branch_exists(git_repo, "feature/orphan")


def test_remove_worktree_missing_path_fails(git_repo: Path) -> None:
    """Removing a non-existent worktree path fails loud."""
    with pytest.raises(WorktreeError) as exc:
        remove_worktree(
            worktree_path=str(git_repo.parent / "myrepo-worktrees" / "ghost"),
            branch=None,
            delete_branch=False,
        )
    assert "does not exist" in exc.value.message


def test_list_worktrees_returns_main_first(git_repo: Path) -> None:
    """With no linked worktrees, only the main tree is listed."""
    result = list_worktrees(repo_path=str(git_repo))
    assert len(result) == 1
    main = result[0]
    assert main.path == str(git_repo)
    assert main.branch == "main"
    assert main.is_main is True
    assert main.detached is False


def test_list_worktrees_includes_linked(git_repo: Path) -> None:
    """A created worktree shows up with its branch and is not flagged main."""
    created = create_worktree(repo_path=str(git_repo), branch_name="feature/login")
    result = list_worktrees(repo_path=str(git_repo))
    # Main first, then the linked worktree.
    assert result[0].is_main is True
    linked = next(w for w in result if not w.is_main)
    assert linked.path == created.worktree_path
    assert linked.branch == "feature/login"
    assert linked.detached is False


def test_list_worktrees_from_linked_resolves_same_list(git_repo: Path) -> None:
    """Listing from inside a linked worktree resolves the main repo's full list."""
    created = create_worktree(repo_path=str(git_repo), branch_name="feature/a")
    # Query from the linked worktree — should still see BOTH worktrees.
    result = list_worktrees(repo_path=created.worktree_path)
    paths = {w.path for w in result}
    assert str(git_repo) in paths
    assert created.worktree_path in paths


def test_list_worktrees_reports_detached_head(git_repo: Path) -> None:
    """A detached-HEAD worktree lists with ``branch=None`` and ``detached=True``."""
    head = _rev_parse(git_repo)
    wt = git_repo.parent / "myrepo-worktrees" / "detached"
    wt.parent.mkdir(parents=True, exist_ok=True)
    # Add a worktree checked out at a bare commit → detached HEAD.
    _git(git_repo, "worktree", "add", "--detach", str(wt), head)
    result = list_worktrees(repo_path=str(git_repo))
    detached = next(w for w in result if w.path == str(wt))
    assert detached.branch is None
    assert detached.detached is True


def test_list_worktrees_non_git_path_fails(tmp_path: Path) -> None:
    """A non-git directory fails loud (the route maps this to 'no worktrees')."""
    plain = (tmp_path / "plain").resolve()
    plain.mkdir()
    with pytest.raises(WorktreeError):
        list_worktrees(repo_path=str(plain))


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "-leading",
        "a..b",
        "a/.hidden",
        "x.lock",
        "x.lock/y",
        "a b",
        "a~b",
        "a:b",
        "/lead",
        "trail/",
    ],
)
def test_validate_branch_name_rejects_bad(bad: str) -> None:
    """Branch names violating git ref-format are rejected before reaching argv."""
    with pytest.raises(WorktreeError):
        validate_branch_name(bad)


@pytest.mark.parametrize("good", ["feature/login", "fix-123", "a/b/c", "release_2", "v1.2"])
def test_validate_branch_name_accepts_good(good: str) -> None:
    """Well-formed branch names pass validation."""
    validate_branch_name(good)  # must not raise
