"""Tests for the host-tunnel receive loop's stat-result handling.

The receive loop resolves ``pending_stats`` futures with a dict rebuilt
from the decoded ``HostStatResultFrame``. Every field the frame carries
must survive that rebuild — the live git-branch refresh (and session
create's branch detection) read ``git_branch`` off that dict, and a
dropped key silently degrades both to "no branch" with no error
anywhere.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from starlette.websockets import WebSocketDisconnect

from omnigent.host.frames import (
    HostHelloFrame,
    HostStatResultFrame,
    decode_host_frame,
    encode_host_frame,
)
from omnigent.server.host_registry import HostRegistry
from omnigent.server.routes.host_tunnel import _receive_loop


class _FakeWS:
    """Starlette-WebSocket-shaped stub feeding scripted incoming messages."""

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._messages = list(messages)

    async def receive(self) -> dict[str, Any]:
        if self._messages:
            return self._messages.pop(0)
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")


def _hello() -> HostHelloFrame:
    return HostHelloFrame(
        version="test",
        frame_protocol_version=1,
        name="stat-tunnel-test",
    )


async def test_receive_loop_resolves_stat_future_with_git_branch() -> None:
    """The stat result dict handed to ``pending_stats`` carries git_branch.

    Regression: the rebuild enumerated status/exists/type/canonical_path/
    error but dropped ``git_branch``, so the host's branch detection
    never reached the server and the live git-branch refresh always fell
    back to the stale recorded value.
    """
    registry = HostRegistry()
    ws = _FakeWS([])
    conn = registry.register(
        "host_stat_tunnel_test",
        ws,  # type: ignore[arg-type] — duck-typed WebSocketLike
        _hello(),
        owner="local",
        workspace_id=0,
    )

    request_id = "req_stat_branch"
    loop = asyncio.get_event_loop()
    future: asyncio.Future[dict[str, Any]] = loop.create_future()
    conn.pending_stats[request_id] = future

    reply = HostStatResultFrame(
        request_id=request_id,
        status="ok",
        exists=True,
        type="directory",
        canonical_path="/repo",
        error=None,
        git_branch="feature/live-branch",
    )
    tunneled_ws = _FakeWS(
        [
            {"type": "websocket.receive", "text": encode_host_frame(reply)},
            {"type": "websocket.disconnect", "code": 1000},
        ]
    )

    with contextlib.suppress(WebSocketDisconnect):
        await asyncio.wait_for(
            _receive_loop(
                tunneled_ws,  # type: ignore[arg-type]
                conn,
                "host_stat_tunnel_test",
                host_store=None,  # type: ignore[arg-type] — stat frames never touch the store
                host_registry=registry,
                runner_exit_reports=None,
                on_runner_exited=None,
                on_host_update=None,
            ),
            timeout=5.0,
        )

    assert future.done()
    result = future.result()
    assert result["status"] == "ok"
    assert result["exists"] is True
    assert result["type"] == "directory"
    assert result["canonical_path"] == "/repo"
    assert result["git_branch"] == "feature/live-branch"

    # The frame round-trips: what the daemon encoded is what was decoded.
    decoded = decode_host_frame(encode_host_frame(reply))
    assert isinstance(decoded, HostStatResultFrame)
    assert decoded.git_branch == "feature/live-branch"
