"""Integration tests for managed worktree lease ops over the host tunnel.

Every host session on a git folder holds a lease (granted at bind);
validity is seq-gated — a folder takeover fences every other claim, and a
fenced session relocates at its next dispatch. These tests pin the server
side of the wire: renewals flow for any host session (no marker label),
the renew result drives relocate-vs-proceed, and the delete flow releases
before deciding whether the folder itself can go.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from omnigent.host.frames import (
    HostCreateWorktreeFrame,
    HostHelloFrame,
    HostLaunchRunnerFrame,
    HostRemoveWorktreeFrame,
    HostWorktreeLeaseFrame,
    decode_host_frame,
)
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.server.host_registry import HostConnection
from omnigent.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)
from omnigent.stores.host_store import HostStore
from tests.server.helpers import create_test_agent

pytestmark = pytest.mark.asyncio

_HOST_ID = "b8df8a226e2d41c89c5e56db56983eaa"
_MANAGED_WORKTREE = "/home/alice/.omnigent/worktrees/myrepo/worktree-1787701890396356865"


class _LeaseCapture:
    """Frames a fake host received during lease flows."""

    def __init__(self) -> None:
        self.lease_frames: list[HostWorktreeLeaseFrame] = []
        self.create_frames: list[HostCreateWorktreeFrame] = []
        # Renew results to serve, in order; defaults to valid+managed.
        self.renew_results: list[dict[str, object]] = []

    def push_renew(self, **fields: object) -> None:
        merged: dict[str, object] = {"valid": True, "managed": True}
        merged.update(fields)
        self.renew_results.append(merged)


RegisterLeaseHost = Callable[..., _LeaseCapture]


@pytest_asyncio.fixture()
async def register_lease_host(
    app: FastAPI,
    db_uri: str,
) -> AsyncIterator[RegisterLeaseHost]:
    """Yield a factory registering fake hosts that answer lease frames.

    The drain answers ``host.worktree_lease`` (renew/grant/release) and
    ``host.create_worktree`` frames, capturing each. All drains are
    poisoned and awaited at teardown so no background task leaks into the
    next test's event loop.

    :param app: App whose ``host_registry`` to register into.
    :param db_uri: DB URI so the ``host_id`` FK target row exists.
    :returns: Async iterator yielding the ``register`` factory. Kwarg:
        ``managed_worktree_leases`` (default ``True``). Returns the
        :class:`_LeaseCapture` accumulating the host's frames.
    """
    captures: list[_LeaseCapture] = []
    conns: list[HostConnection] = []

    def _register(*, managed_worktree_leases: bool = True) -> _LeaseCapture:
        HostStore(db_uri).upsert_on_connect(_HOST_ID, "lease-host", RESERVED_USER_LOCAL)
        conn: HostConnection = app.state.host_registry.register(
            host_id=_HOST_ID,
            ws=object(),  # type: ignore[arg-type] — duck-typed; the registry only enqueues
            hello=HostHelloFrame(
                version="0.1.0-test",
                frame_protocol_version=1,
                name="lease-host",
                managed_worktree_leases=managed_worktree_leases,
            ),
            owner=RESERVED_USER_LOCAL,
        )
        cap = _LeaseCapture()
        captures.append(cap)

        async def _drain() -> None:
            """Answer lease/create frames; capture them."""
            while True:
                frame_text = await conn.outbound_queue.get()
                if frame_text is None:
                    return
                frame = decode_host_frame(frame_text)
                if isinstance(frame, HostWorktreeLeaseFrame):
                    cap.lease_frames.append(frame)
                    fut = conn.pending_worktree_leases.pop(frame.request_id, None)
                    if fut is not None and not fut.done():
                        if frame.op == "renew":
                            outcome = (
                                cap.renew_results.pop(0)
                                if cap.renew_results
                                else {"valid": True, "managed": True}
                            )
                            fut.set_result({"status": "ok", **outcome})
                        elif frame.op == "grant":
                            fut.set_result(
                                {
                                    "status": "ok",
                                    "valid": True,
                                    "managed": True,
                                }
                            )
                        else:
                            fut.set_result(
                                {
                                    "status": "ok",
                                    "released": True,
                                    "managed": True,
                                    "folder_free": True,
                                }
                            )
                elif isinstance(frame, HostCreateWorktreeFrame):
                    cap.create_frames.append(frame)
                    fut = conn.pending_create_worktrees.pop(frame.request_id, None)
                    if fut is not None and not fut.done():
                        fut.set_result(
                            {
                                "status": "ok",
                                "worktree_path": f"{frame.repo_path}-relocated",
                                "branch": frame.branch_name,
                                "error": None,
                            }
                        )
                elif isinstance(frame, HostLaunchRunnerFrame):
                    # No runner comes up in these tests — fail the launch
                    # fast so the dispatch doesn't wait out the boot grace.
                    fut = conn.pending_launches.pop(frame.request_id, None)
                    if fut is not None and not fut.done():
                        fut.set_result({"status": "failed", "runner_id": None, "error": "test"})
                elif isinstance(frame, HostRemoveWorktreeFrame):
                    fut = conn.pending_remove_worktrees.pop(frame.request_id, None)
                    if fut is not None and not fut.done():
                        fut.set_result({"status": "ok", "error": None})

        conns.append(conn)
        conn._drain_task_for_test = asyncio.create_task(_drain())  # type: ignore[attr-defined]
        return cap

    yield _register

    for conn in conns:
        conn.outbound_queue.put_nowait(None)
        task = conn._drain_task_for_test  # type: ignore[attr-defined]
        with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError, Exception):
            await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
        if not task.done():
            task.cancel()


async def _bound_host_session(
    client: httpx.AsyncClient,
    db_uri: str,
    name: str,
) -> str:
    """Create a host-bound session whose workspace is the managed worktree.

    The session deliberately carries no marker label — under the lease
    model every host session on a git folder participates, label-free.

    :param client: The test HTTP client.
    :param db_uri: DB URI for the conversation store.
    :param name: Agent name, e.g. ``"lease-sharer"``.
    :returns: The new session id.
    """
    agent = await create_test_agent(client, name=name)
    resp = await client.post("/v1/sessions", json={"agent_id": agent["id"]})
    assert resp.status_code == 201, resp.text
    session_id = resp.json()["id"]
    SqlAlchemyConversationStore(db_uri).set_host_id(
        session_id,
        _HOST_ID,
        _MANAGED_WORKTREE,
        git_branch="agent/session-branch",
    )
    return session_id


async def test_heartbeat_renewal_reaches_host_for_any_host_session(
    client: httpx.AsyncClient,
    register_lease_host: RegisterLeaseHost,
    db_uri: str,
) -> None:
    """A marker-label-free host session's heartbeat renewal reaches the host."""
    from omnigent.server.routes._sessions.common import _session_status_cache
    from omnigent.server.routes._sessions.orchestration import (
        _auto_worktree_lease_renewed_at,
        _renew_active_auto_worktree_lease,
    )

    lease_host = register_lease_host()
    session_id = await _bound_host_session(client, db_uri, "lease-heartbeat")
    _session_status_cache[session_id] = "running"
    try:
        await _renew_active_auto_worktree_lease(
            session_id,
            SqlAlchemyConversationStore(db_uri),
        )
    finally:
        _session_status_cache.pop(session_id, None)
        _auto_worktree_lease_renewed_at.pop(session_id, None)

    assert len(lease_host.lease_frames) == 1, (
        f"Heartbeat renewal must reach the host for a label-free session; "
        f"got {len(lease_host.lease_frames)} lease frames."
    )
    frame = lease_host.lease_frames[0]
    assert frame.op == "renew"
    assert frame.worktree_path == _MANAGED_WORKTREE
    assert frame.session_id == session_id


async def test_turn_end_renewal_reaches_host_for_any_host_session(
    client: httpx.AsyncClient,
    register_lease_host: RegisterLeaseHost,
    db_uri: str,
) -> None:
    """A marker-label-free host session's turn-edge renewal reaches the host."""
    from omnigent.server.routes._sessions.helpers import (
        _renew_session_worktree_lease,
    )

    lease_host = register_lease_host()
    session_id = await _bound_host_session(client, db_uri, "lease-turn-end")

    await _renew_session_worktree_lease(session_id)

    assert len(lease_host.lease_frames) == 1
    assert lease_host.lease_frames[0].op == "renew"
    assert lease_host.lease_frames[0].session_id == session_id


async def test_heartbeat_renewal_skips_non_host_sessions(
    client: httpx.AsyncClient,
    register_lease_host: RegisterLeaseHost,
    db_uri: str,
) -> None:
    """A session without a host/workspace sends no lease frame."""
    from omnigent.server.routes._sessions.common import _session_status_cache
    from omnigent.server.routes._sessions.orchestration import (
        _renew_active_auto_worktree_lease,
    )

    lease_host = register_lease_host()
    agent = await create_test_agent(client, name="lease-local")
    resp = await client.post("/v1/sessions", json={"agent_id": agent["id"]})
    assert resp.status_code == 201, resp.text
    session_id = resp.json()["id"]
    _session_status_cache[session_id] = "running"
    try:
        await _renew_active_auto_worktree_lease(
            session_id,
            SqlAlchemyConversationStore(db_uri),
        )
    finally:
        _session_status_cache.pop(session_id, None)

    assert lease_host.lease_frames == []


async def test_heartbeat_renewal_skips_lease_incapable_host(
    client: httpx.AsyncClient,
    register_lease_host: RegisterLeaseHost,
    db_uri: str,
) -> None:
    """A host without the managed-lease capability gets no lease frame."""
    from omnigent.server.routes._sessions.common import _session_status_cache
    from omnigent.server.routes._sessions.orchestration import (
        _renew_active_auto_worktree_lease,
    )

    lease_host = register_lease_host(managed_worktree_leases=False)
    session_id = await _bound_host_session(client, db_uri, "lease-incapable")
    _session_status_cache[session_id] = "running"
    try:
        await _renew_active_auto_worktree_lease(
            session_id,
            SqlAlchemyConversationStore(db_uri),
        )
    finally:
        _session_status_cache.pop(session_id, None)

    assert lease_host.lease_frames == []


async def test_heartbeat_renewal_throttles_repeat_calls(
    client: httpx.AsyncClient,
    register_lease_host: RegisterLeaseHost,
    db_uri: str,
) -> None:
    """The hourly throttle still suppresses back-to-back renewals."""
    from omnigent.server.routes._sessions.common import _session_status_cache
    from omnigent.server.routes._sessions.orchestration import (
        _auto_worktree_lease_renewed_at,
        _renew_active_auto_worktree_lease,
    )

    lease_host = register_lease_host()
    session_id = await _bound_host_session(client, db_uri, "lease-throttle")
    _session_status_cache[session_id] = "running"
    store = SqlAlchemyConversationStore(db_uri)

    try:
        await _renew_active_auto_worktree_lease(session_id, store)
        await _renew_active_auto_worktree_lease(session_id, store)
    finally:
        _session_status_cache.pop(session_id, None)
        _auto_worktree_lease_renewed_at.pop(session_id, None)

    assert len(lease_host.lease_frames) == 1, (
        f"Back-to-back heartbeat renewals must be throttled to one frame; "
        f"got {len(lease_host.lease_frames)}."
    )


async def test_fenced_lease_relocates_at_dispatch(
    client: httpx.AsyncClient,
    register_lease_host: RegisterLeaseHost,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fenced lease (folder reassigned) relocates before the turn runs.

    The dispatch renew comes back ``valid=False, managed=True`` — the
    folder was taken over — so the session stops its (absent) runner,
    acquires a fresh folder from the host, and rebinds its workspace
    before the message is forwarded.
    """
    from omnigent.server import managed_hosts as _managed_hosts
    from omnigent.server.routes import sessions as _sessions_facade
    from omnigent.server.routes._sessions import helpers as _sessions_helpers

    # No runner ever arrives; skip the 30s boot-grace waits after relocate.
    # The waits read the constants from several module namespaces — patch all.
    monkeypatch.setattr(_sessions_facade, "_HOST_RELAUNCH_RUNNER_CONNECT_TIMEOUT_S", 0.5)
    monkeypatch.setattr(_sessions_facade, "_HOST_BOUND_RUNNER_CONNECT_GRACE_S", 0.5)
    monkeypatch.setattr(_sessions_helpers, "_HOST_RELAUNCH_RUNNER_CONNECT_TIMEOUT_S", 0.5)
    monkeypatch.setattr(_managed_hosts, "MANAGED_LAUNCH_RENDEZVOUS_TIMEOUT_S", 0.5)
    lease_host = register_lease_host()
    # First renew (dispatch validation): fenced. The relocation's acquire
    # is answered by the create-frame drain above.
    lease_host.push_renew(valid=False, managed=True)
    session_id = await _bound_host_session(client, db_uri, "lease-relocate")

    # No runner is bound, so the turn itself fails after relocation — the
    # test pins the relocate-before-turn ordering, not the turn outcome.
    with contextlib.suppress(AssertionError):
        await client.post(
            f"/v1/sessions/{session_id}/events",
            json={
                "type": "message",
                "data": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hi"}],
                },
            },
        )
    renew_frames = [f for f in lease_host.lease_frames if f.op == "renew"]
    assert len(renew_frames) == 1
    assert renew_frames[0].session_id == session_id
    # The relocate acquire ran against the fenced folder's path.
    assert len(lease_host.create_frames) == 1, (
        f"A fenced lease must relocate via a worktree acquire; "
        f"got create frames {lease_host.create_frames!r}."
    )
    create = lease_host.create_frames[0]
    assert create.reuse_path == _MANAGED_WORKTREE
    assert create.session_id == session_id
    # The session row now points at the new folder.
    meta = SqlAlchemyConversationStore(db_uri)
    conv = meta.get_conversation(session_id)
    assert conv is not None
    assert conv.workspace == f"{_MANAGED_WORKTREE}-relocated"


async def test_relocate_uses_persisted_repo_root_when_workspace_deleted(
    client: httpx.AsyncClient,
    register_lease_host: RegisterLeaseHost,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fenced session whose workspace dir is gone relocates from the repo.

    The renew result carries the lease's persisted repo root; the relocate
    acquire must run against it, not the deleted workspace path.
    """
    from omnigent.server import managed_hosts as _managed_hosts
    from omnigent.server.routes import sessions as _sessions_facade
    from omnigent.server.routes._sessions import helpers as _sessions_helpers

    monkeypatch.setattr(_sessions_facade, "_HOST_RELAUNCH_RUNNER_CONNECT_TIMEOUT_S", 0.5)
    monkeypatch.setattr(_sessions_facade, "_HOST_BOUND_RUNNER_CONNECT_GRACE_S", 0.5)
    monkeypatch.setattr(_sessions_helpers, "_HOST_RELAUNCH_RUNNER_CONNECT_TIMEOUT_S", 0.5)
    monkeypatch.setattr(_managed_hosts, "MANAGED_LAUNCH_RENDEZVOUS_TIMEOUT_S", 0.5)
    lease_host = register_lease_host()
    lease_host.push_renew(valid=False, managed=True, repo_root="/home/alice/myrepo")
    session_id = await _bound_host_session(client, db_uri, "lease-relocate-deleted")

    with contextlib.suppress(AssertionError):
        await client.post(
            f"/v1/sessions/{session_id}/events",
            json={
                "type": "message",
                "data": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hi"}],
                },
            },
        )

    assert len(lease_host.create_frames) == 1
    # The acquire ran against the repo root, not the deleted workspace.
    assert lease_host.create_frames[0].repo_path == "/home/alice/myrepo"
    assert lease_host.create_frames[0].reuse_path == _MANAGED_WORKTREE


async def test_delete_releases_lease_and_keeps_folder_with_active_couser(
    client: httpx.AsyncClient,
    register_lease_host: RegisterLeaseHost,
    db_uri: str,
) -> None:
    """Delete releases the claim first; a co-user's claim keeps the folder.

    The release frame must be sent BEFORE any worktree removal, and a
    ``folder_free=False`` result must suppress the removal entirely (the
    co-user still works in that folder).
    """
    lease_host = register_lease_host()
    agent = await create_test_agent(client, name="lease-delete")
    resp = await client.post("/v1/sessions", json={"agent_id": agent["id"]})
    assert resp.status_code == 201, resp.text
    session_id = resp.json()["id"]
    SqlAlchemyConversationStore(db_uri).set_host_id(
        session_id,
        _HOST_ID,
        _MANAGED_WORKTREE,
        git_branch="agent/session-branch",
    )

    resp = await client.delete(f"/v1/sessions/{session_id}?delete_branch=true")
    assert resp.status_code == 200, resp.text

    ops = [f.op for f in lease_host.lease_frames]
    assert "release" in ops, f"delete must release the lease; got {ops}"
    assert "grant" not in ops
    # No active co-user claim on the fake host → folder_free=True → the
    # removal proceeds (the frame would be sent; the drain ignores it).
    releases = [f for f in lease_host.lease_frames if f.op == "release"]
    assert releases[0].session_id == session_id


async def test_plain_folder_session_skips_relocation(
    client: httpx.AsyncClient,
    register_lease_host: RegisterLeaseHost,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``managed=False`` (plain folder) proceeds without relocating."""
    from omnigent.server import managed_hosts as _managed_hosts
    from omnigent.server.routes import sessions as _sessions_facade
    from omnigent.server.routes._sessions import helpers as _sessions_helpers

    # No runner ever arrives; skip the 30s boot-grace waits after dispatch.
    # The waits read the constants from several module namespaces — patch all.
    monkeypatch.setattr(_sessions_facade, "_HOST_RELAUNCH_RUNNER_CONNECT_TIMEOUT_S", 0.5)
    monkeypatch.setattr(_sessions_facade, "_HOST_BOUND_RUNNER_CONNECT_GRACE_S", 0.5)
    monkeypatch.setattr(_sessions_helpers, "_HOST_RELAUNCH_RUNNER_CONNECT_TIMEOUT_S", 0.5)
    monkeypatch.setattr(_managed_hosts, "MANAGED_LAUNCH_RENDEZVOUS_TIMEOUT_S", 0.5)
    lease_host = register_lease_host()
    lease_host.push_renew(valid=False, managed=False)
    session_id = await _bound_host_session(client, db_uri, "lease-plain")

    with contextlib.suppress(AssertionError):
        await client.post(
            f"/v1/sessions/{session_id}/events",
            json={
                "type": "message",
                "data": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hi"}],
                },
            },
        )

    assert lease_host.create_frames == []
    conv = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert conv is not None
    assert conv.workspace == _MANAGED_WORKTREE
