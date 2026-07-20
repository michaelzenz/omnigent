"""Ambient bridge from standalone Codex sessions into Omnigent.

Watches ``~/.codex`` rollout JSONL files created by the real Codex app/CLI
and mirrors them into Omnigent as imported codex-native sessions. Initial
history is imported via ``POST /v1/imports``; subsequent turns are appended
through ``ambient_codex_sync`` events. Poll cursors live on the server.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from omnigent.ambient_codex import HOST_AMBIENT_ID_HEADER
from omnigent.host.polling.context import PollContext
from omnigent.host.polling.pollers.codex_config import (
    _DEFAULT_POLL_INTERVAL_S,
    codex_ambient_sync_enabled,
    load_codex_poller_config,
)
from omnigent.host.polling.pollers.codex_local import CodexLocalSubPoller
from omnigent.host.polling.pollers.codex_remote import CodexRemoteSubPoller
from omnigent.host.polling.pollers.codex_state import (
    BridgeState,
    TrackedRollout,
    hydrate_bridge_state,
)
from omnigent.ssh_connections_store import read_ssh_connections

# Backward-compatible aliases for tests and callers.
_BridgeState = BridgeState
_TrackedRollout = TrackedRollout
_hydrate_bridge_state = hydrate_bridge_state


def _poll_context_for_client(client) -> PollContext:
    host_id = client.headers.get(HOST_AMBIENT_ID_HEADER, "")
    return PollContext(
        server_url=str(client.base_url),
        host_id=host_id if isinstance(host_id, str) else "",
        client=client,
    )


async def _poll_codex_ambient_once(client, *, state: BridgeState, codex_home: Path) -> BridgeState:
    """Scan Codex rollouts once and mirror any new history."""
    ctx = _poll_context_for_client(client)
    config = load_codex_poller_config()
    local = CodexLocalSubPoller(codex_home=codex_home)
    state = await local.poll_once(ctx, state)
    for profile in read_ssh_connections():
        if not profile.codex_remote:
            continue
        remote = CodexRemoteSubPoller(
            profile,
            interval_s=config.remote_interval_s,
            backoff_cap_s=config.remote_backoff_cap_s,
        )
        state = await remote.poll_once(ctx, state)
    return state


async def _prune_deleted_codex_sessions(client, *, state: BridgeState, codex_home: Path) -> BridgeState:
    """Drop tracked local threads whose Codex rollout no longer exists."""
    local = CodexLocalSubPoller(codex_home=codex_home)
    return await local.prune_deleted_sessions(client, state=state)


async def run_codex_ambient_bridge(
    server_url: str,
    *,
    host_id: str,
    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    codex_home: Path | None = None,
) -> None:
    """Poll standalone Codex rollouts and mirror them into Omnigent."""
    from omnigent.host.polling import CodexAmbientPoller, PollScheduler

    scheduler = PollScheduler(server_url=server_url, host_id=host_id)
    scheduler.register(
        CodexAmbientPoller(
            codex_home=codex_home,
            poll_interval_s=poll_interval_s,
        )
    )
    await scheduler.start()
    try:
        while True:
            await asyncio.sleep(3600.0)
    except asyncio.CancelledError:
        await scheduler.stop()
        raise


__all__ = [
    "_BridgeState",
    "_TrackedRollout",
    "_hydrate_bridge_state",
    "_poll_codex_ambient_once",
    "_prune_deleted_codex_sessions",
    "codex_ambient_sync_enabled",
    "load_codex_poller_config",
    "run_codex_ambient_bridge",
]
