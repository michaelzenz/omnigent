"""
Integration tests for ``POST /v1/hosts/{id}/worktrees``.

Wires up a real host tunnel + REST router pair, drives a fake host
that auto-replies to ``host.create_worktree`` frames, and exercises
the endpoint's contract end-to-end: managed creation with a session
lease, plain creation without one, and the failure mappings (unknown
host, unknown session, host-reported git failure).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import pytest
from asgiref.testing import ApplicationCommunicator
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from omnigent.host.frames import (
    HostCreateWorktreeFrame,
    HostCreateWorktreeResultFrame,
    HostHelloFrame,
    HostStatFrame,
    HostStatResultFrame,
    decode_host_frame,
    encode_host_frame,
)
from omnigent.server.host_registry import HostRegistry
from omnigent.server.routes.host_tunnel import create_host_tunnel_router
from omnigent.server.routes.hosts import create_hosts_router
from omnigent.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)
from omnigent.stores.host_store import HostStore

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.flaky(reruns=2, reruns_delay=1),
]

_HOST_ID = "7c1fda8f5e302e51cee65f7094f3d49b"
_HOST_NAME = "wt-create-test-laptop"

# Wire-up state: (app, replies, seen_create_frames, fake-host stat table).
_WtSetupWithStats = tuple[
    FastAPI,
    dict[tuple[str, str], dict[str, Any]],
    list[HostCreateWorktreeFrame],
    dict[str, dict[str, Any]],
]


def _websocket_scope(path: str) -> dict[str, object]:
    """Build a minimal ASGI WebSocket scope.

    :param path: WebSocket path, e.g. ``"/v1/hosts/X/tunnel"``.
    :returns: ASGI scope dict.
    """
    return {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "ws",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "subprotocols": [],
    }


def _hello_text(
    name: str = _HOST_NAME,
    *,
    managed_worktree_leases: bool = True,
) -> str:
    """Encode a hello frame for tests.

    :param name: Host name reported in the hello frame.
    :param managed_worktree_leases: Whether the fake host advertises the
        managed-worktree lease protocol (the auto-new-worktree gate).
    :returns: JSON-encoded hello frame.
    """
    return encode_host_frame(
        HostHelloFrame(
            version="0.1.0-test",
            frame_protocol_version=1,
            name=name,
            managed_worktree_leases=managed_worktree_leases,
        )
    )


@pytest.fixture()
def wt_app(
    db_uri: str,
) -> tuple[FastAPI, HostRegistry, HostStore, SqlAlchemyConversationStore]:
    """
    App with host tunnel + REST routes for worktree-create tests.

    :param db_uri: SQLite URI fixture.
    :returns: (app, registry, host_store, conv_store).
    """
    registry = HostRegistry()
    host_store = HostStore(db_uri)
    conv_store = SqlAlchemyConversationStore(db_uri)
    app = FastAPI()
    app.include_router(create_host_tunnel_router(registry, host_store), prefix="/v1")
    app.include_router(
        create_hosts_router(registry, host_store, conv_store),
        prefix="/v1",
    )
    return app, registry, host_store, conv_store


@pytest.fixture()
async def wt_setup(
    wt_app: tuple[FastAPI, HostRegistry, HostStore, SqlAlchemyConversationStore],
) -> AsyncIterator[_WtSetupWithStats]:
    """
    Connect a mock host and auto-reply to create_worktree frames.

    Tests register fake replies in ``replies`` keyed by
    ``(repo_path, branch_name)`` before calling the REST endpoint, and
    can inspect ``seen`` for the frames the server actually sent. The
    auto-replier decodes outbound frames and resolves the matching
    pending future — the same wiring host_tunnel.py does in production.

    :param wt_app: The fixture above.
    :returns: Async iterator yielding (app, replies, seen).
    """
    app, registry, _hs, _cs = wt_app
    path = f"/v1/hosts/{_HOST_ID}/tunnel"
    comm = ApplicationCommunicator(app, _websocket_scope(path))
    await comm.send_input({"type": "websocket.connect"})
    accepted = await comm.receive_output(timeout=1.0)
    assert accepted["type"] == "websocket.accept"
    await comm.send_input({"type": "websocket.receive", "text": _hello_text()})
    while registry.get(_HOST_ID) is None:
        await asyncio.sleep(0.01)

    replies: dict[tuple[str, str], dict[str, Any]] = {}
    # repo_path (as sent) → canonical path the fake host reports back.
    stats: dict[str, dict[str, str | bool | None]] = {
        # Both spellings the tests post — tilde (canonicalized by the
        # fake host) and the canonical absolute path.
        "~/repo": {"exists": True, "type": "directory", "canonical_path": "/Users/corey/repo"},
        "/Users/corey/repo": {
            "exists": True,
            "type": "directory",
            "canonical_path": "/Users/corey/repo",
        },
    }
    seen: list[HostCreateWorktreeFrame] = []
    stop_drain = asyncio.Event()

    async def _drain() -> None:
        """Drain outbound WS frames and feed back the configured reply."""
        while not stop_drain.is_set():
            try:
                output = await comm.receive_output(timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if output.get("type") != "websocket.send":
                continue
            text = output.get("text")
            if not isinstance(text, str):
                continue
            frame = decode_host_frame(text)
            if isinstance(frame, HostStatFrame):
                stat = stats.get(frame.path)
                if stat is None:
                    stat = {"exists": False, "type": None, "canonical_path": None}
                stat_type = stat.get("type")
                stat_canonical = stat.get("canonical_path")
                await comm.send_input(
                    {
                        "type": "websocket.receive",
                        "text": encode_host_frame(
                            HostStatResultFrame(
                                request_id=frame.request_id,
                                status="ok",
                                exists=bool(stat.get("exists")),
                                type=stat_type if isinstance(stat_type, str) else None,
                                canonical_path=(
                                    stat_canonical if isinstance(stat_canonical, str) else None
                                ),
                            )
                        ),
                    }
                )
                continue
            if not isinstance(frame, HostCreateWorktreeFrame):
                continue
            seen.append(frame)
            reply = replies.get((frame.repo_path, frame.branch_name))
            if reply is None:
                reply_frame = HostCreateWorktreeResultFrame(
                    request_id=frame.request_id,
                    status="failed",
                    error="branch already exists",
                )
            else:
                reply_frame = HostCreateWorktreeResultFrame(
                    request_id=frame.request_id,
                    status=reply.get("status", "ok"),
                    worktree_path=reply.get("worktree_path"),
                    branch=reply.get("branch"),
                    error=reply.get("error"),
                )
            await comm.send_input(
                {"type": "websocket.receive", "text": encode_host_frame(reply_frame)}
            )

    drain_task = asyncio.create_task(_drain())
    try:
        yield app, replies, seen, stats
    finally:
        stop_drain.set()
        try:
            await asyncio.wait_for(drain_task, timeout=1.0)
        except asyncio.TimeoutError:
            drain_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await comm.send_input({"type": "websocket.disconnect", "code": 1000})


async def test_create_worktree_managed_with_session(
    wt_setup: _WtSetupWithStats,
    wt_app: tuple[FastAPI, HostRegistry, HostStore, SqlAlchemyConversationStore],
) -> None:
    """With session_id the managed path runs and returns the folder + branch."""
    app, replies, seen, _stats = wt_setup
    conv_store = wt_app[3]
    conv = conv_store.create_conversation()
    replies[("/Users/corey/repo", "feature/login")] = {
        "worktree_path": "/Users/corey/.omnigent/worktrees/repo/repo-ab12cd34-1700000000",
        "branch": "feature/login",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/v1/hosts/{_HOST_ID}/worktrees",
            json={
                "repo_path": "/Users/corey/repo",
                "branch_name": "feature/login",
                "base_branch": "main",
                "session_id": conv.id,
            },
        )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["worktree_path"].endswith("1700000000")
    assert payload["branch"] == "feature/login"
    # Managed creation: the frame carries the lease holder and reuse flag.
    assert len(seen) == 1
    assert seen[0].auto_reuse is True
    assert seen[0].session_id == conv.id
    assert seen[0].base_branch == "main"


async def test_create_worktree_without_session_is_plain(
    wt_setup: _WtSetupWithStats,
) -> None:
    """Without session_id the frame carries auto_reuse=False and no session id."""
    app, replies, seen, _stats = wt_setup
    replies[("/Users/corey/repo", "spike")] = {
        "worktree_path": "/Users/corey/.omnigent/worktrees/repo/repo-cd34ef56-1700000001",
        "branch": "spike",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/v1/hosts/{_HOST_ID}/worktrees",
            json={"repo_path": "/Users/corey/repo", "branch_name": "spike"},
        )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["worktree_path"].endswith("1700000001")
    assert payload["branch"] == "spike"
    assert len(seen) == 1
    assert seen[0].auto_reuse is False
    assert seen[0].session_id is None


async def test_create_worktree_canonicalizes_tilde_repo_path(
    wt_setup: _WtSetupWithStats,
) -> None:
    """A tilde repo_path is stat-canonicalized before the create frame is sent."""
    app, replies, seen, _stats = wt_setup
    replies[("/Users/corey/repo", "feature/x")] = {
        "worktree_path": "/Users/corey/.omnigent/worktrees/repo/repo-ee56ff78-1700000002",
        "branch": "feature/x",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/v1/hosts/{_HOST_ID}/worktrees",
            json={"repo_path": "~/repo", "branch_name": "feature/x"},
        )
    assert resp.status_code == 200, resp.text
    # The host stat ran with the tilde path (only the host expands ~), and
    # the create frame carried the canonical absolute path back.
    assert len(seen) == 1
    assert seen[0].repo_path == "/Users/corey/repo"


async def test_create_worktree_missing_repo_path_400(
    wt_setup: _WtSetupWithStats,
) -> None:
    """A repo_path the host cannot stat as a directory yields 400."""
    app, _replies, seen, _stats = wt_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/v1/hosts/{_HOST_ID}/worktrees",
            json={"repo_path": "/nope/missing", "branch_name": "feature/x"},
        )
    assert resp.status_code == 400, resp.text
    assert "not a directory" in resp.json()["detail"]
    assert seen == []


async def test_create_worktree_host_failure_400(wt_setup: _WtSetupWithStats) -> None:
    """A host-reported git failure (no reply registered) maps to 400."""
    app, _replies, _seen, _stats = wt_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/v1/hosts/{_HOST_ID}/worktrees",
            json={"repo_path": "/Users/corey/repo", "branch_name": "dup"},
        )
    assert resp.status_code == 400, resp.text
    assert "branch already exists" in resp.json()["detail"]


async def test_create_worktree_unknown_session_404(wt_setup: _WtSetupWithStats) -> None:
    """A session_id that names no conversation yields 404 before any frame."""
    app, _replies, seen, _stats = wt_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/v1/hosts/{_HOST_ID}/worktrees",
            json={
                "repo_path": "/Users/corey/repo",
                "branch_name": "feature/x",
                "session_id": "conv_missing",
            },
        )
    assert resp.status_code == 404, resp.text
    assert seen == []


async def test_create_worktree_invalid_branch_400(wt_setup: _WtSetupWithStats) -> None:
    """A malformed branch name is rejected server-side before any frame."""
    app, _replies, seen, _stats = wt_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/v1/hosts/{_HOST_ID}/worktrees",
            json={"repo_path": "/Users/corey/repo", "branch_name": "bad branch.."},
        )
    assert resp.status_code == 400, resp.text
    # Pins the branch-validation path (not the earlier stat gate).
    assert "branch name" in resp.json()["detail"]
    assert seen == []


async def test_create_worktree_unmanaged_host_400(
    wt_app: tuple[FastAPI, HostRegistry, HostStore, SqlAlchemyConversationStore],
) -> None:
    """A host without managed-worktree leases rejects managed creation."""
    app, registry, _hs, conv_store = wt_app
    conv = conv_store.create_conversation()
    path = f"/v1/hosts/{_HOST_ID}/tunnel"
    comm = ApplicationCommunicator(app, _websocket_scope(path))
    await comm.send_input({"type": "websocket.connect"})
    accepted = await comm.receive_output(timeout=1.0)
    assert accepted["type"] == "websocket.accept"
    await comm.send_input(
        {"type": "websocket.receive", "text": _hello_text(managed_worktree_leases=False)}
    )
    while registry.get(_HOST_ID) is None:
        await asyncio.sleep(0.01)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/v1/hosts/{_HOST_ID}/worktrees",
                json={
                    "repo_path": "/Users/corey/repo",
                    "branch_name": "feature/x",
                    "session_id": conv.id,
                },
            )
        assert resp.status_code == 400, resp.text
        assert "upgraded" in resp.json()["detail"]
    finally:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await comm.send_input({"type": "websocket.disconnect", "code": 1000})


async def test_create_worktree_unknown_host_404(
    wt_app: tuple[FastAPI, HostRegistry, HostStore, SqlAlchemyConversationStore],
) -> None:
    """An unknown host id yields 404 (existence is gated before the offline check)."""
    app, _reg, _hs, _cs = wt_app
    unknown_id = "1e498b8cd21815434fca9278770ff1d2"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/v1/hosts/{unknown_id}/worktrees",
            json={"repo_path": "/Users/corey/repo", "branch_name": "feature/x"},
        )
    assert resp.status_code == 404, resp.text
