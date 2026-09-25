"""
Integration tests for git worktree creation on the dedicated per-session
bind endpoint ``POST /v1/hosts/{host_id}/runners`` (``launch_runner``).

This is the endpoint the fork-resume flow uses to bind an already-existing
(unbound) session to a host + directory. Unlike ``POST /v1/sessions`` —
which creates the worktree before the conversation row exists —
``launch_runner`` operates on a row that already exists, so it must create
the worktree at bind time and roll it back if the bind/launch fails.

Drives the endpoint through the full app and a fake host that auto-replies
to the host control frames (``host.stat`` for workspace validation,
``host.create_worktree``, ``host.launch_runner``, and
``host.remove_worktree`` for rollback). See designs/SESSION_GIT_WORKTREE.md.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from omnigent.host.frames import (
    HostCreateWorktreeFrame,
    HostHelloFrame,
    HostLaunchRunnerFrame,
    HostRemoveWorktreeFrame,
    HostStatFrame,
    HostWorktreeLeaseFrame,
    decode_host_frame,
)
from omnigent.runtime import session_stream
from omnigent.runtime.agent_cache import AgentCache
from omnigent.server.app import create_app
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.server.host_registry import HostConnection
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.comment_store.sqlalchemy_store import SqlAlchemyCommentStore
from omnigent.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)
from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore
from omnigent.stores.host_store import HostStore
from tests.server.helpers import create_test_agent

pytestmark = pytest.mark.asyncio

_HOST_ID = "51dc949aba31e24ca8f047d6fba31a0d"
_SOURCE_REPO = "/Users/alice/myrepo"


@pytest.fixture()
def app(runtime_init: None, db_uri: str, tmp_path: Path) -> FastAPI:
    """FastAPI app wired WITH ``host_store`` so ``launch_runner`` can
    resolve host ownership and launch a runner.

    Overrides the shared ``app`` fixture (which passes
    ``host_store=None`` and so can't run the dedicated launch endpoint).
    The shared ``client`` fixture depends on this ``app``.

    :param runtime_init: Initializes the runtime + mock LLM.
    :param db_uri: SQLite database URI.
    :param tmp_path: Pytest temp dir for artifacts and cache.
    :returns: A configured FastAPI app with host routes mounted.
    """
    artifact_store = LocalArtifactStore(str(tmp_path / "artifacts"))
    return create_app(
        agent_store=SqlAlchemyAgentStore(db_uri),
        file_store=SqlAlchemyFileStore(db_uri),
        conversation_store=SqlAlchemyConversationStore(db_uri),
        artifact_store=artifact_store,
        agent_cache=AgentCache(
            artifact_store=artifact_store,
            cache_dir=tmp_path / "cache",
        ),
        comment_store=SqlAlchemyCommentStore(db_uri),
        host_store=HostStore(db_uri),
    )


class _FakeWebSocket:
    """Minimal WebSocket stand-in (the registry only enqueues)."""

    async def send_text(self, data: str) -> None:
        """No-op send — frames flow through the outbound queue.

        :param data: JSON-encoded frame text (ignored).
        """


@dataclass
class _HostCapture:
    """
    Frames a fake host received during one ``launch_runner`` call.

    :param create: ``host.create_worktree`` frames received.
    :param launch: ``host.launch_runner`` frames received.
    :param remove: ``host.remove_worktree`` frames received (a non-empty
        list proves the rollback path fired).
    :param leases: ``host.worktree_lease`` frames received (the launch
        path's managed-folder boundary probe).
    """

    create: list[HostCreateWorktreeFrame] = field(default_factory=list)
    launch: list[HostLaunchRunnerFrame] = field(default_factory=list)
    remove: list[HostRemoveWorktreeFrame] = field(default_factory=list)
    leases: list[HostWorktreeLeaseFrame] = field(default_factory=list)


# register(*, create_status=, create_error=, launch_status=) -> _HostCapture
RegisterHost = Callable[..., _HostCapture]


@pytest_asyncio.fixture()
async def register_host(
    app: FastAPI,
    db_uri: str,
) -> AsyncIterator[RegisterHost]:
    """Yield a factory that registers a fake host with a replying drain.

    The drain answers ``host.stat`` (workspace validation passes),
    ``host.create_worktree``, ``host.launch_runner``, and
    ``host.remove_worktree`` — capturing each into a :class:`_HostCapture`.
    Every drain is poisoned and awaited at teardown so no background task
    leaks into the next test's event loop.

    :param app: App whose ``host_registry`` to register into.
    :param db_uri: DB URI so the ``host_id`` FK target row exists.
    :returns: Async iterator yielding a ``register`` factory. Kwargs:
        ``create_status`` (``"ok"``/``"failed"``), ``create_error``
        (host failure detail), ``launch_status``
        (``"launched"``/``"failed"``). Returns the :class:`_HostCapture`
        accumulating frames the host received.
    """
    conns: list[HostConnection] = []

    def _register(
        *,
        create_status: str = "ok",
        create_error: str | None = None,
        launch_status: str = "launched",
        managed_worktree_leases: bool = False,
        lease_managed: bool = True,
        lease_claim: bool = True,
        lease_status: str = "ok",
    ) -> _HostCapture:
        HostStore(db_uri).upsert_on_connect(_HOST_ID, "wt-host", RESERVED_USER_LOCAL)
        conn = app.state.host_registry.register(
            host_id=_HOST_ID,
            ws=_FakeWebSocket(),  # type: ignore[arg-type] — duck-typed
            hello=HostHelloFrame(
                version="0.1.0-test",
                frame_protocol_version=1,
                name="wt-host",
                managed_worktree_leases=managed_worktree_leases,
            ),
            owner=RESERVED_USER_LOCAL,
        )
        cap = _HostCapture()

        async def _drain() -> None:
            """Answer stat/create/launch/remove frames; capture them."""
            while True:
                frame_text = await conn.outbound_queue.get()
                if frame_text is None:
                    return
                frame = decode_host_frame(frame_text)
                if isinstance(frame, HostStatFrame):
                    fut = conn.pending_stats.pop(frame.request_id, None)
                    if fut is not None and not fut.done():
                        fut.set_result(
                            {
                                "status": "ok",
                                "exists": True,
                                "type": "directory",
                                "canonical_path": frame.path,
                                "error": None,
                            }
                        )
                elif isinstance(frame, HostCreateWorktreeFrame):
                    cap.create.append(frame)
                    fut = conn.pending_create_worktrees.pop(frame.request_id, None)
                    if fut is not None and not fut.done():
                        if create_status == "ok":
                            dirname = frame.branch_name.replace("/", "-")
                            fut.set_result(
                                {
                                    "status": "ok",
                                    "worktree_path": f"{frame.repo_path}-worktrees/{dirname}",
                                    "branch": frame.branch_name,
                                    "error": None,
                                }
                            )
                        else:
                            fut.set_result(
                                {
                                    "status": "failed",
                                    "worktree_path": None,
                                    "branch": None,
                                    "error": create_error,
                                }
                            )
                elif isinstance(frame, HostLaunchRunnerFrame):
                    cap.launch.append(frame)
                    fut = conn.pending_launches.pop(frame.request_id, None)
                    if fut is not None and not fut.done():
                        fut.set_result(
                            {
                                "status": launch_status,
                                "runner_id": (
                                    "runner_from_host" if launch_status == "launched" else None
                                ),
                                "error": None if launch_status == "launched" else "boom",
                            }
                        )
                elif isinstance(frame, HostWorktreeLeaseFrame):
                    cap.leases.append(frame)
                    fut = conn.pending_worktree_leases.pop(frame.request_id, None)
                    if fut is not None and not fut.done():
                        fut.set_result(
                            {
                                "status": lease_status,
                                # check op: managed = folder is a registered
                                # managed worktree; valid = the requesting
                                # session holds an unexpired claim on it.
                                "valid": lease_claim,
                                "managed": lease_managed,
                                "error": None if lease_status == "ok" else "host lease error",
                            }
                        )
                elif isinstance(frame, HostRemoveWorktreeFrame):
                    cap.remove.append(frame)
                    fut = conn.pending_remove_worktrees.pop(frame.request_id, None)
                    if fut is not None and not fut.done():
                        fut.set_result({"status": "ok", "error": None})

        conn._drain_task_for_test = asyncio.create_task(_drain())  # type: ignore[attr-defined]
        conns.append(conn)
        return cap

    yield _register

    for conn in conns:
        conn.outbound_queue.put_nowait(None)
        task = conn._drain_task_for_test  # type: ignore[attr-defined]
        with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError, Exception):
            await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
        if not task.done():
            task.cancel()


async def _bare_session(
    client: httpx.AsyncClient,
    name: str,
    *,
    os_env: dict[str, object] | None = None,
) -> str:
    """Create an unbound session (agent only, no host/workspace).

    :param client: The test HTTP client.
    :param name: Agent name to create.
    :param os_env: Optional ``os_env:`` block for the agent spec, e.g.
        ``{"cwd": "/Users/alice/sandbox"}`` to pin a workspace boundary.
    :returns: The new session id.
    """
    agent = await create_test_agent(client, name=name, os_env=os_env)
    resp = await client.post("/v1/sessions", json={"agent_id": agent["id"]})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _launch(
    client: httpx.AsyncClient,
    session_id: str,
    *,
    git: dict[str, object] | None = None,
) -> httpx.Response:
    """POST the dedicated per-session bind+launch endpoint.

    :param client: The test HTTP client.
    :param session_id: Existing session to bind.
    :param git: Optional ``git`` block. Create mode, e.g.
        ``{"branch_name": "feature/x"}``; bind mode, e.g.
        ``{"branch_name": "feature/x", "existing_worktree": True}``.
    :returns: The raw HTTP response.
    """
    body: dict[str, object] = {"session_id": session_id, "workspace": _SOURCE_REPO}
    if git is not None:
        body["git"] = git
    return await client.post(f"/v1/hosts/{_HOST_ID}/runners", json=body)


async def test_launch_runner_with_git_creates_worktree_and_persists_branch(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """``launch_runner`` with a ``git`` block creates a worktree off the
    source repo and binds the session to the worktree path + branch.

    Proves the new worktree step on the dedicated bind endpoint: the
    request's branch reaches ``host.create_worktree``, and the resulting
    worktree path + branch are persisted on the (previously unbound)
    session row via the extended ``set_host_id``. Without the new code the
    session would bind to the source repo with ``git_branch=NULL``.
    """
    cap = register_host()
    session_id = await _bare_session(client, "wt-launch-agent")

    resp = await _launch(
        client, session_id, git={"branch_name": "feature/login", "base_branch": "main"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["runner_id"]  # a runner was bound

    # The host received exactly one create-worktree frame off the source
    # repo, carrying the requested branch + base ref.
    assert len(cap.create) == 1, f"expected one create_worktree frame, got {len(cap.create)}"
    assert cap.create[0].repo_path == _SOURCE_REPO
    assert cap.create[0].branch_name == "feature/login"
    assert cap.create[0].base_branch == "main"
    # Success path: no rollback.
    assert cap.remove == [], "worktree was rolled back on a successful launch"

    # Persisted row: workspace is the worktree path (NOT the source repo),
    # git_branch is the new branch, host_id is bound. A NULL git_branch
    # here means set_host_id didn't receive/persist the branch.
    conv = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert conv is not None
    assert conv.workspace == f"{_SOURCE_REPO}-worktrees/feature-login"
    assert conv.git_branch == "feature/login"
    assert conv.host_id == _HOST_ID


async def test_launch_runner_without_git_binds_source_dir_no_worktree(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """Without a ``git`` block the endpoint binds the source directory
    directly and creates no worktree (the same-directory resume path).

    Pins that the new worktree code is inert when ``git`` is omitted:
    no ``host.create_worktree`` frame, workspace stays the source repo,
    and ``git_branch`` stays NULL.
    """
    cap = register_host()
    session_id = await _bare_session(client, "no-wt-agent")

    resp = await _launch(client, session_id, git=None)
    assert resp.status_code == 200, resp.text

    assert cap.create == [], "no worktree should be created without a git block"
    conv = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert conv is not None
    assert conv.workspace == _SOURCE_REPO
    assert conv.git_branch is None
    assert conv.host_id == _HOST_ID


async def test_launch_runner_with_existing_worktree_persists_without_creating(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """``git.existing_worktree`` binds the existing worktree dir and records
    its branch without creating a worktree (the existing-worktree resume path).

    The workspace is already a worktree, so no ``host.create_worktree``
    frame is sent; ``branch_name`` is persisted as ``git_branch`` so the
    sidebar shows it and the opt-in delete flow can offer to remove it.
    """
    cap = register_host()
    session_id = await _bare_session(client, "existing-wt-agent")

    resp = await _launch(
        client,
        session_id,
        git={"branch_name": "feature/existing", "existing_worktree": True},
    )
    assert resp.status_code == 200, resp.text

    assert cap.create == [], "no worktree should be created for an existing worktree"
    conv = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert conv is not None
    assert conv.workspace == _SOURCE_REPO
    assert conv.git_branch == "feature/existing"
    assert conv.host_id == _HOST_ID


async def test_launch_runner_rolls_back_worktree_on_launch_failure(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """When the host fails the launch, the just-created worktree is
    rolled back AND the runner binding is cleared so the picker can retry.

    The worktree is created (status ok) but the launch reports
    ``failed`` → the endpoint returns 502, sends a
    ``host.remove_worktree`` for the created worktree, and clears the
    session's ``runner_id``. If the rollback were missing, ``cap.remove``
    would be empty; if the binding weren't cleared, ``runner_id`` would
    stay set and a retry would dead-end on the atomic ``set_runner_id``
    CAS with "session already has a runner bound" (the whole point of the
    fork-resume picker is that the user can retry after a failed bind).
    """
    cap = register_host(launch_status="failed")
    session_id = await _bare_session(client, "wt-rollback-agent")

    resp = await _launch(client, session_id, git={"branch_name": "feature/x"})

    # Launch failed → 502 (host fault), as the no-git path already does.
    assert resp.status_code == 502, resp.text
    # The worktree was created, then removed (rollback fired) for the same path.
    assert len(cap.create) == 1
    assert len(cap.remove) == 1, "expected a rollback remove_worktree frame after launch failure"
    created_path = f"{_SOURCE_REPO}-worktrees/feature-x"
    assert cap.remove[0].worktree_path == created_path
    assert cap.remove[0].delete_branch is True  # orphan branch also cleaned up

    # The session is fully unbound so the DB matches the host's actual
    # state (worktree removed) and a retry starts clean. A non-None
    # runner_id would stick the session as "already bound"; a leftover
    # workspace/git_branch would point at the deleted worktree/branch and
    # could wrongly trigger worktree-cleanup paths (git_branch IS NOT NULL).
    conv = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert conv is not None
    assert conv.runner_id is None, (
        "runner_id should be cleared after a failed launch so the picker can "
        f"rebind; got {conv.runner_id!r} (retry would 400 'already has a runner bound')"
    )
    assert conv.host_id is None, f"host_id should be cleared on rollback; got {conv.host_id!r}"
    assert conv.workspace is None, (
        f"workspace should be cleared (worktree was removed); got {conv.workspace!r}"
    )
    assert conv.git_branch is None, (
        f"git_branch should be cleared (branch was removed); got {conv.git_branch!r}"
    )


async def test_launch_runner_retry_succeeds_after_failed_launch(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """A second bind succeeds after the first launch failed.

    End-to-end proof of the cleared-binding fix: a failed launch (502)
    must leave the session re-bindable. The retry creates a fresh
    worktree and binds the runner. Without clearing ``runner_id`` on the
    first failure, this retry returns 400 "session already has a runner
    bound" — the dead-end the fork-resume picker would otherwise hit.
    """
    register_host(launch_status="failed")
    session_id = await _bare_session(client, "wt-retry-agent")

    first = await _launch(client, session_id, git={"branch_name": "feature/x"})
    assert first.status_code == 502, first.text

    # Re-register the host to launch successfully this time (newest-wins
    # replaces the failing connection), then retry the bind.
    cap_ok = register_host(launch_status="launched")
    second = await _launch(client, session_id, git={"branch_name": "feature/y"})

    # Retry binds cleanly — proves runner_id was released by the failure.
    assert second.status_code == 200, second.text
    assert second.json()["runner_id"]
    assert len(cap_ok.create) == 1, "retry created a fresh worktree off the source repo"
    conv = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert conv is not None
    assert conv.workspace == f"{_SOURCE_REPO}-worktrees/feature-y"
    assert conv.git_branch == "feature/y"


async def test_launch_runner_rollback_preserves_existing_branch(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """A failed launch after an ``existing_branch`` recreate must NOT
    delete the branch.

    The deleted-worktree recreate path checks out a branch that predates
    the request (it may carry unpushed commits). Rollback still removes
    the just-recreated directory, but ``delete_branch`` must be False —
    ``git branch -D`` here would destroy the user's work.
    """
    cap = register_host(launch_status="failed")
    session_id = await _bare_session(client, "wt-existing-branch-rollback-agent")

    resp = await _launch(
        client,
        session_id,
        git={"branch_name": "feature/x", "existing_branch": True},
    )

    assert resp.status_code == 502, resp.text
    assert len(cap.create) == 1
    assert cap.create[0].existing_branch is True
    assert len(cap.remove) == 1, "rollback must still remove the recreated worktree dir"
    assert cap.remove[0].delete_branch is False, (
        "rollback of an existing-branch recreate must preserve the user's "
        "pre-existing branch (unpushed commits would be lost)"
    )
    # The session is still fully unbound so a retry starts clean.
    conv = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert conv is not None
    assert conv.runner_id is None
    assert conv.git_branch is None


async def test_launch_runner_auto_create_generates_branch_and_labels(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """``auto_create`` on the launch endpoint names the branch server-side
    and stamps the managed-worktree labels.

    This is the switch-host auto-worktree path: the server generates a
    branch name (the bounded, fail-open AI call — with the test runtime's
    mock LLM the generated name is whatever the mock returns, so the
    assertion is on the flow, not the exact name), creates the worktree
    with a lease owner, and binds the session to the worktree path.
    """
    cap = register_host(managed_worktree_leases=True)
    session_id = await _bare_session(client, "wt-auto-create-agent")

    resp = await _launch(
        client,
        session_id,
        git={"auto_create": True, "branch_name_prompt": "fix the login retry flake"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["runner_id"]

    # Exactly one create frame, off the source repo, carrying the
    # server-generated branch and a lease owned by this session.
    assert len(cap.create) == 1, f"expected one create_worktree frame, got {len(cap.create)}"
    assert cap.create[0].repo_path == _SOURCE_REPO
    assert cap.create[0].branch_name  # generated, non-empty
    assert cap.remove == [], "worktree was rolled back on a successful launch"

    # The session row points at the worktree with the generated branch.
    # Labels are gone from the lease model: the folder's managed state and
    # source repo live in the host registry (the lease was granted
    # host-side by the acquire flow).
    conv = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert conv is not None
    expected_dirname = cap.create[0].branch_name.replace("/", "-")
    assert conv.workspace == f"{_SOURCE_REPO}-worktrees/{expected_dirname}"
    assert conv.git_branch == cap.create[0].branch_name
    assert conv.host_id == _HOST_ID
    assert not any(key.startswith("omnigent.auto_worktree") for key in conv.labels), (
        "auto_worktree labels are removed from the lease model"
    )


async def test_launch_runner_auto_create_requires_lease_capable_host(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
) -> None:
    """``auto_create`` on a host without managed-worktree leases is a 400.

    The managed lease is what lets a later session adopt an auto worktree
    instead of duplicating it; a host that predates the capability cannot
    provide it, so the request is rejected up front rather than silently
    creating an unmanaged worktree.
    """
    register_host()  # default hello: managed_worktree_leases=False
    session_id = await _bare_session(client, "wt-auto-create-old-host-agent")

    resp = await _launch(client, session_id, git={"auto_create": True})

    assert resp.status_code == 400, resp.text
    assert "upgraded" in resp.json()["detail"]


async def test_launch_runner_boundary_admits_managed_worktree(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The W6 boundary check admits managed worktree folders the session
    already holds a claim on.

    Relocation into an Omnigent-created auto worktree must work even
    though the folder sits outside the agent's ``os_env.cwd`` boundary:
    the launch path re-checks via the host's managed-worktree registry
    (the read-only claim probe) and binds the canonical path. An
    arbitrary out-of-boundary folder still fails — see the rejection
    tests below.
    """
    from omnigent.server.routes import _workspace_validation

    cap = register_host(managed_worktree_leases=True)
    session_id = await _bare_session(client, "wt-carveout-agent")
    managed_path = f"{_SOURCE_REPO}-worktrees/feature-login"

    async def _boundary_reject(
        *,
        host_registry: object,
        host_id: str,
        workspace: str,
        spec_cwd: str | None,
        host_name_for_errors: str | None = None,
    ) -> object:
        """Stand in for validate_workspace: always boundary-reject."""
        raise _workspace_validation.WorkspaceValidationError(
            f"workspace '{workspace}' is outside the agent's required path '{spec_cwd}'",
            reason=_workspace_validation.WorkspaceValidationError.OUTSIDE_BOUNDARY,
            canonical_workspace=workspace,
        )

    monkeypatch.setattr(_workspace_validation, "validate_workspace", _boundary_reject)

    resp = await client.post(
        f"/v1/hosts/{_HOST_ID}/runners",
        json={"session_id": session_id, "workspace": managed_path},
    )
    assert resp.status_code == 200, resp.text
    # The carve-out probed the host's registry exactly once (read-only
    # check op) and bound the canonical (echoed) worktree path — the
    # launch frame carries it too.
    assert len(cap.leases) == 1
    assert cap.leases[0].worktree_path == managed_path
    assert cap.leases[0].op == "check"
    assert len(cap.launch) == 1
    assert cap.launch[0].workspace == managed_path
    conv = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert conv is not None
    assert conv.workspace == managed_path


async def test_launch_runner_boundary_still_rejects_plain_folder(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-managed folder outside the boundary is still a 400.

    The carve-out must stay an Omnigent-worktree-only allowance: when the
    host's registry does not know the folder (lease grant replies
    managed=False), the boundary rejection stands.
    """
    from omnigent.server.routes import _workspace_validation

    cap = register_host(managed_worktree_leases=True, lease_managed=False)
    session_id = await _bare_session(client, "wt-carveout-plain-agent")

    async def _boundary_reject(
        *,
        host_registry: object,
        host_id: str,
        workspace: str,
        spec_cwd: str | None,
        host_name_for_errors: str | None = None,
    ) -> object:
        """Stand in for validate_workspace: always boundary-reject."""
        raise _workspace_validation.WorkspaceValidationError(
            f"workspace '{workspace}' is outside the agent's required path '{spec_cwd}'",
            reason=_workspace_validation.WorkspaceValidationError.OUTSIDE_BOUNDARY,
            canonical_workspace=workspace,
        )

    monkeypatch.setattr(_workspace_validation, "validate_workspace", _boundary_reject)

    resp = await client.post(
        f"/v1/hosts/{_HOST_ID}/runners",
        json={"session_id": session_id, "workspace": "/etc"},
    )
    assert resp.status_code == 400, resp.text
    assert "outside the agent's required path" in resp.json()["detail"]
    # The probe ran (host advertises leases) but the folder is not managed.
    assert len(cap.leases) == 1


async def test_launch_runner_boundary_no_probe_without_leases(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host without managed-worktree leases never probes — plain 400."""
    from omnigent.server.routes import _workspace_validation

    cap = register_host()  # default hello: managed_worktree_leases=False
    session_id = await _bare_session(client, "wt-carveout-old-host-agent")

    async def _boundary_reject(
        *,
        host_registry: object,
        host_id: str,
        workspace: str,
        spec_cwd: str | None,
        host_name_for_errors: str | None = None,
    ) -> object:
        """Stand in for validate_workspace: always boundary-reject."""
        raise _workspace_validation.WorkspaceValidationError(
            f"workspace '{workspace}' is outside the agent's required path '{spec_cwd}'",
            reason=_workspace_validation.WorkspaceValidationError.OUTSIDE_BOUNDARY,
            canonical_workspace=workspace,
        )

    monkeypatch.setattr(_workspace_validation, "validate_workspace", _boundary_reject)

    resp = await client.post(
        f"/v1/hosts/{_HOST_ID}/runners",
        json={"session_id": session_id, "workspace": f"{_SOURCE_REPO}-worktrees/x"},
    )
    assert resp.status_code == 400, resp.text
    assert "outside the agent's required path" in resp.json()["detail"]
    assert cap.leases == [], "the probe must short-circuit on lease-incapable hosts"


async def test_launch_runner_boundary_rejects_foreign_managed_worktree(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A managed folder the session holds NO claim on stays rejected.

    The carve-out requires the requesting session's own claim (from
    creating the folder or binding it through a managed path) — another
    session's worktree must not become a boundary escape.
    """
    from omnigent.server.routes import _workspace_validation

    cap = register_host(managed_worktree_leases=True, lease_claim=False)
    session_id = await _bare_session(client, "wt-carveout-foreign-agent")

    async def _boundary_reject(
        *,
        host_registry: object,
        host_id: str,
        workspace: str,
        spec_cwd: str | None,
        host_name_for_errors: str | None = None,
    ) -> object:
        """Stand in for validate_workspace: always boundary-reject."""
        raise _workspace_validation.WorkspaceValidationError(
            f"workspace '{workspace}' is outside the agent's required path '{spec_cwd}'",
            reason=_workspace_validation.WorkspaceValidationError.OUTSIDE_BOUNDARY,
            canonical_workspace=workspace,
        )

    monkeypatch.setattr(_workspace_validation, "validate_workspace", _boundary_reject)

    resp = await client.post(
        f"/v1/hosts/{_HOST_ID}/runners",
        json={"session_id": session_id, "workspace": f"{_SOURCE_REPO}-worktrees/other"},
    )
    assert resp.status_code == 400, resp.text
    assert "outside the agent's required path" in resp.json()["detail"]
    assert len(cap.leases) == 1
    assert cap.launch == [], "a rejected launch must not reach the host launch frame"


async def test_launch_runner_boundary_probe_host_failure_keeps_boundary_400(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host-reported check failure is fail-closed: 400 with the boundary
    message (the folder could not be confirmed as claimed)."""
    from omnigent.server.routes import _workspace_validation

    cap = register_host(managed_worktree_leases=True, lease_status="failed")
    session_id = await _bare_session(client, "wt-carveout-probe-fail-agent")

    async def _boundary_reject(
        *,
        host_registry: object,
        host_id: str,
        workspace: str,
        spec_cwd: str | None,
        host_name_for_errors: str | None = None,
    ) -> object:
        """Stand in for validate_workspace: always boundary-reject."""
        raise _workspace_validation.WorkspaceValidationError(
            f"workspace '{workspace}' is outside the agent's required path '{spec_cwd}'",
            reason=_workspace_validation.WorkspaceValidationError.OUTSIDE_BOUNDARY,
            canonical_workspace=workspace,
        )

    monkeypatch.setattr(_workspace_validation, "validate_workspace", _boundary_reject)

    resp = await client.post(
        f"/v1/hosts/{_HOST_ID}/runners",
        json={"session_id": session_id, "workspace": f"{_SOURCE_REPO}-worktrees/x"},
    )
    assert resp.status_code == 400, resp.text
    assert "outside the agent's required path" in resp.json()["detail"]
    assert len(cap.leases) == 1
    assert cap.launch == []


async def test_launch_runner_boundary_probe_unavailable_maps_to_409(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A probe whose connection was replaced mid-request is a 409 — infra,
    not a boundary verdict."""
    from omnigent.server.routes import _workspace_validation

    cap = register_host(managed_worktree_leases=True)
    session_id = await _bare_session(client, "wt-carveout-probe-down-agent")

    async def _boundary_reject_and_drop(
        *,
        host_registry: object,
        host_id: str,
        workspace: str,
        spec_cwd: str | None,
        host_name_for_errors: str | None = None,
    ) -> object:
        """Boundary-reject AND replace the host connection, so the probe's
        captured connection is stale (send_text raises ConnectionError)."""
        # The host row already exists (register_host upserted it); a fresh
        # register poisons the old connection's queue and supersedes it.
        app.state.host_registry.register(
            host_id=_HOST_ID,
            ws=_FakeWebSocket(),  # type: ignore[arg-type]
            hello=HostHelloFrame(
                version="0.1.0-test",
                frame_protocol_version=1,
                name="wt-host",
                managed_worktree_leases=True,
            ),
            owner=RESERVED_USER_LOCAL,
        )
        raise _workspace_validation.WorkspaceValidationError(
            f"workspace '{workspace}' is outside the agent's required path '{spec_cwd}'",
            reason=_workspace_validation.WorkspaceValidationError.OUTSIDE_BOUNDARY,
            canonical_workspace=workspace,
        )

    monkeypatch.setattr(
        _workspace_validation,
        "validate_workspace",
        _boundary_reject_and_drop,  # type: ignore[arg-type]
    )

    resp = await client.post(
        f"/v1/hosts/{_HOST_ID}/runners",
        json={"session_id": session_id, "workspace": f"{_SOURCE_REPO}-worktrees/x"},
    )
    assert resp.status_code == 409, resp.text
    assert "connection lost" in resp.json()["detail"]
    assert cap.launch == []


async def test_launch_runner_managed_explicit_branch_uses_pool(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """``git.managed`` with an explicit branch creates a LEASED worktree.

    The sys_session_create_worktree tool path: same atomic create + bind
    + launch as the plain git flow, but the create frame carries
    ``auto_reuse`` + the session id, so the host may reuse a clean
    managed folder from its pool and the folder is leased to this
    session. The branch is the caller-supplied one (no AI naming).
    """
    cap = register_host(managed_worktree_leases=True)
    session_id = await _bare_session(client, "wt-managed-branch-agent")

    resp = await _launch(
        client,
        session_id,
        git={"branch_name": "feature/mgd", "base_branch": "main", "managed": True},
    )
    assert resp.status_code == 200, resp.text

    assert len(cap.create) == 1
    assert cap.create[0].repo_path == _SOURCE_REPO
    assert cap.create[0].branch_name == "feature/mgd"
    assert cap.create[0].base_branch == "main"
    assert cap.create[0].auto_reuse is True
    assert cap.create[0].session_id == session_id
    assert cap.remove == [], "worktree was rolled back on a successful launch"

    conv = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert conv is not None
    assert conv.workspace == f"{_SOURCE_REPO}-worktrees/feature-mgd"
    assert conv.git_branch == "feature/mgd"


async def test_launch_runner_relocation_releases_old_claim(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """Rebinding to a different folder releases the session's old claim.

    Without this, the session's lease on its previous managed worktree
    keeps the folder fenced out of the reuse pool until the lease's TTL
    lapses. The UI's switch-host flow rides this same endpoint, so it
    gets the release for free.
    """
    cap = register_host(managed_worktree_leases=True)
    session_id = await _bare_session(client, "wt-relocate-release-agent")
    old_ws = f"{_SOURCE_REPO}-worktrees/old-task"
    conv_store = SqlAlchemyConversationStore(db_uri)
    conv_store.set_host_id(session_id, _HOST_ID, old_ws, "old-task-branch")

    resp = await _launch(client, session_id)  # different workspace: _SOURCE_REPO
    assert resp.status_code == 200, resp.text

    release_frames = [f for f in cap.leases if f.op == "release"]
    assert len(release_frames) == 1, (
        f"expected exactly one release frame, got {[f.op for f in cap.leases]}"
    )
    # The launch itself still happened.
    assert len(cap.launch) == 1
    conv = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert conv is not None
    assert conv.workspace == _SOURCE_REPO


async def test_launch_runner_same_workspace_keeps_claim(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """Re-launching at the SAME folder does not release the claim.

    A runner recovery / re-bind to the folder the session still occupies
    must not drop its claim — the folder would briefly rejoin the reuse
    pool while the session still lives in it.
    """
    cap = register_host(managed_worktree_leases=True)
    session_id = await _bare_session(client, "wt-same-folder-agent")
    SqlAlchemyConversationStore(db_uri).set_host_id(
        session_id, _HOST_ID, _SOURCE_REPO, None
    )

    resp = await _launch(client, session_id)  # same workspace: _SOURCE_REPO
    assert resp.status_code == 200, resp.text
    assert cap.leases == [], "same-folder re-launch must not touch the lease"
    assert len(cap.launch) == 1


async def test_launch_runner_cross_host_release_is_best_effort(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """A cross-host move attempts the release on the OLD host only.

    The old host here is not connected (offline), so the release is
    skipped silently — the move still succeeds and the stale lease
    self-expires.
    """
    cap = register_host(managed_worktree_leases=True)
    session_id = await _bare_session(client, "wt-cross-host-agent")
    other_host_id = "0aa98877665544332211ffeeddccbbaa"
    SqlAlchemyConversationStore(db_uri).set_host_id(
        session_id, other_host_id, "/Users/alice/other-host-folder", None
    )

    resp = await _launch(client, session_id)  # target: _HOST_ID, different folder
    assert resp.status_code == 200, resp.text
    # Nothing was sent to the NEW host's registry for the old claim, and
    # the launch succeeded despite the old host being unreachable.
    assert cap.leases == []
    assert len(cap.launch) == 1
    conv = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert conv is not None
    assert conv.host_id == _HOST_ID


async def test_launch_runner_managed_creation_keeps_new_claim(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """Managed creation from an existing workspace does NOT release the claim.

    The session was living in a managed worktree; creating a new one
    (git.managed) re-points its single lease record to the new folder,
    which already vacates the old one. A release here would instead drop
    the NEW folder's claim — exactly wrong.
    """
    cap = register_host(managed_worktree_leases=True)
    session_id = await _bare_session(client, "wt-managed-no-release-agent")
    old_ws = f"{_SOURCE_REPO}-worktrees/first-task"
    SqlAlchemyConversationStore(db_uri).set_host_id(session_id, _HOST_ID, old_ws, "first-task")

    resp = await _launch(
        client,
        session_id,
        git={"branch_name": "second-task", "base_branch": "main", "managed": True},
    )
    assert resp.status_code == 200, resp.text
    # The create frame ran (auto_reuse + session lease) but no release
    # frame: the acquire's grant superseded the old claim already.
    assert len(cap.create) == 1
    assert cap.create[0].auto_reuse is True
    assert cap.create[0].session_id == session_id
    assert cap.leases == [], (
        f"managed creation must not release — got {[f.op for f in cap.leases]}"
    )
    assert len(cap.launch) == 1
    conv = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert conv is not None
    assert conv.workspace == f"{_SOURCE_REPO}-worktrees/second-task"
    assert conv.git_branch == "second-task"


# ── Boundary escalation (human approval) ──────────────────────────


async def _drain_one_elicitation(
    session_id: str,
    timeout_s: float = 5.0,
) -> dict[str, object]:
    """Subscribe to the session stream and capture the first elicitation.

    The launch endpoint publishes the ``response.elicitation_request``
    event before parking on the verdict future, so subscribing here is
    the simplest way to learn the id.

    :param session_id: Session to subscribe to.
    :param timeout_s: Max seconds to wait for the event.
    :returns: The captured elicitation_request event dict.
    """
    async with asyncio.timeout(timeout_s):
        async for event in session_stream.subscribe(session_id):
            if event.get("type") == "response.elicitation_request":
                return event
    raise AssertionError("subscribe loop ended without an elicitation event")


async def test_launch_runner_boundary_escalation_accept(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """Out-of-boundary creation + escalate → approval card → accept → launch.

    The human verdict is the only thing that lifts the W6 boundary: the
    agent cannot self-approve (the resolve URL is LEVEL_EDIT-gated and
    the future lives server-side), and nothing is persisted — the
    verdict is consumed by the very launch that asked.
    """
    cap = register_host(managed_worktree_leases=True, lease_claim=False)
    session_id = await _bare_session(
        client,
        "wt-escalate-accept-agent",
        os_env={"cwd": "/Users/alice/sandbox"},
    )
    drain_task = asyncio.create_task(_drain_one_elicitation(session_id))
    launch_task = asyncio.create_task(
        _launch(
            client,
            session_id,
            git={"branch_name": "esc/feat", "managed": True, "escalate_boundary": True},
        )
    )
    event = await drain_task
    assert event.get("elicitation_id")
    verdict = await client.post(
        f"/v1/sessions/{session_id}/elicitations/{event['elicitation_id']}/resolve",
        json={"action": "accept"},
    )
    assert verdict.status_code == 202, verdict.text

    resp = await launch_task
    assert resp.status_code == 200, resp.text
    # The managed creation ran past the boundary.
    assert len(cap.create) == 1
    assert cap.create[0].branch_name == "esc/feat"
    conv = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert conv is not None
    assert conv.workspace == f"{_SOURCE_REPO}-worktrees/esc-feat"


async def test_launch_runner_boundary_escalation_decline(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
) -> None:
    """An explicit decline fails the launch with 403 — nothing created."""
    cap = register_host(managed_worktree_leases=True, lease_claim=False)
    session_id = await _bare_session(
        client,
        "wt-escalate-decline-agent",
        os_env={"cwd": "/Users/alice/sandbox"},
    )
    drain_task = asyncio.create_task(_drain_one_elicitation(session_id))
    launch_task = asyncio.create_task(
        _launch(
            client,
            session_id,
            git={"branch_name": "esc/deny", "managed": True, "escalate_boundary": True},
        )
    )
    event = await drain_task
    verdict = await client.post(
        f"/v1/sessions/{session_id}/elicitations/{event['elicitation_id']}/resolve",
        json={"action": "decline"},
    )
    assert verdict.status_code == 202, verdict.text

    resp = await launch_task
    assert resp.status_code == 403, resp.text
    assert "declined" in resp.json()["detail"]
    assert cap.create == [], "a declined escalation must not create anything"
    assert cap.launch == []


async def test_launch_runner_boundary_escalation_timeout(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No verdict before the timeout → 400, fail-closed."""
    import omnigent.server.routes.hosts as hosts_module

    cap = register_host(managed_worktree_leases=True, lease_claim=False)
    session_id = await _bare_session(
        client,
        "wt-escalate-timeout-agent",
        os_env={"cwd": "/Users/alice/sandbox"},
    )
    monkeypatch.setattr(hosts_module, "_BOUNDARY_ESCALATION_TIMEOUT_S", 0.05)

    resp = await _launch(
        client,
        session_id,
        git={"branch_name": "esc/slow", "managed": True, "escalate_boundary": True},
    )
    assert resp.status_code == 400, resp.text
    assert "no response to the boundary-approval prompt" in resp.json()["detail"]
    assert cap.create == []


async def test_launch_runner_in_boundary_skips_escalation(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
) -> None:
    """In-boundary folder + escalate flag → no elicitation, straight launch."""
    from omnigent.runtime import pending_elicitations

    cap = register_host(managed_worktree_leases=True)
    session_id = await _bare_session(
        client,
        "wt-escalate-inside-agent",
        os_env={"cwd": "/Users/alice"},
    )
    resp = await _launch(
        client,
        session_id,
        git={"branch_name": "inside/x", "managed": True, "escalate_boundary": True},
    )
    assert resp.status_code == 200, resp.text
    assert pending_elicitations.count_for(session_id) == 0
    assert len(cap.create) == 1


async def test_launch_runner_carve_out_precedes_escalation(
    register_host: RegisterHost,
    client: httpx.AsyncClient,
    db_uri: str,
) -> None:
    """A claimed managed folder passes WITHOUT prompting even with the flag.

    The claim carve-out runs first: the session already holds the claim,
    so relocating into its own worktree needs no human approval.
    """
    from omnigent.runtime import pending_elicitations

    cap = register_host(managed_worktree_leases=True, lease_claim=True)
    session_id = await _bare_session(
        client,
        "wt-escalate-carveout-agent",
        os_env={"cwd": "/Users/alice/sandbox"},
    )
    own_wt = f"{_SOURCE_REPO}-worktrees/mine"
    SqlAlchemyConversationStore(db_uri).set_host_id(session_id, _HOST_ID, own_wt, "mine")

    resp = await client.post(
        f"/v1/hosts/{_HOST_ID}/runners",
        json={"session_id": session_id, "workspace": own_wt},
    )
    assert resp.status_code == 200, resp.text
    assert pending_elicitations.count_for(session_id) == 0
    # The carve-out probe ran (check op); no approval card was needed.
    assert len(cap.leases) == 1
    assert cap.leases[0].op == "check"
###RC=0
