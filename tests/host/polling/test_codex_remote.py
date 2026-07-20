"""Tests for remote Codex sub-pollers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from omnigent.host.polling.context import PollContext
from omnigent.host.polling.pollers.codex_remote import CodexRemoteSubPoller
from omnigent.host.polling.pollers.codex_state import BridgeState
from omnigent.ssh_connections_store import SshConnectionProfile
from omnigent.ssh_remote import RemoteCodexRollout


def _profile() -> SshConnectionProfile:
    return SshConnectionProfile(
        id="conn-1",
        label="Arca",
        alias="arca.ssh",
        created_at="2026-01-01T00:00:00Z",
        codex_remote=True,
    )


def test_remote_sub_poller_backoff_skips_until_due() -> None:
    poller = CodexRemoteSubPoller(_profile(), interval_s=5.0, backoff_cap_s=30.0)
    poller.record_outcome(100.0, success=False)
    assert poller.is_due(101.0) is False
    assert poller.is_due(110.0) is True


@pytest.mark.asyncio
async def test_remote_sub_poller_skips_import_after_409(tmp_path: Path) -> None:
    session_id = "019e96aa-0be2-7343-8d3b-6f914d60936b"
    rollout_path = f"/home/user/.codex/sessions/rollout-2026-07-15T12-00-00-{session_id}.jsonl"
    poller = CodexRemoteSubPoller(_profile(), interval_s=0.01, backoff_cap_s=1.0)
    state = BridgeState(threads={})
    ctx = PollContext(
        server_url="http://test",
        host_id="a" * 32,
        client=httpx.AsyncClient(base_url="http://test"),
    )
    rollout = RemoteCodexRollout(path=rollout_path, mtime_ms=9_999_999_999_000, size=12)

    with (
        patch(
            "omnigent.host.polling.pollers.codex_remote.ssh_remote_codex_rollouts",
            new=AsyncMock(return_value=[rollout]),
        ),
        patch(
            "omnigent.host.polling.pollers.codex_remote.rollout_is_recent",
            return_value=True,
        ),
        patch(
            "omnigent.host.polling.pollers.codex_remote.ssh_remote_rollout_to_tempfile",
            new=AsyncMock(return_value=tmp_path / "remote.jsonl"),
        ),
        patch(
            "omnigent.host.polling.pollers.codex_remote.load_codex_session_from_rollout",
            return_value=type("Imported", (), {"workspace": "/repo", "items": ()})(),
        ),
        patch(
            "omnigent.host.polling.pollers.codex_remote.import_codex_session",
            new=AsyncMock(return_value=None),
        ) as import_mock,
    ):
        (tmp_path / "remote.jsonl").write_text("{}", encoding="utf-8")
        await poller.poll_once_delta(ctx, state)
        await poller.poll_once_delta(ctx, state)

    assert import_mock.await_count == 1
    await ctx.client.aclose()
