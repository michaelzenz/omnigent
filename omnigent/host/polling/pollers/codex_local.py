"""Local filesystem Codex rollout polling."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from omnigent.host.polling.context import PollContext
from omnigent.host.polling.pollers.codex_state import (
    BridgeState,
    TrackedRollout,
    delete_omnigent_session,
    import_codex_session,
    post_ambient_codex_sync,
    rollout_is_recent,
    tracked_state_key,
)
from omnigent.session_import.codex_rollout import (
    active_codex_rollout_path,
    iter_codex_rollout_paths,
    read_codex_rollout_from_offset,
    thread_id_from_rollout_path,
)
from omnigent.session_import.local import load_codex_session
from omnigent.session_import.models import SessionImportNotFoundError

_logger = logging.getLogger(__name__)


class CodexLocalSubPoller:
    """Scan ``~/.codex`` rollouts on the host machine."""

    def __init__(self, *, codex_home: Path) -> None:
        self._codex_home = codex_home

    async def poll_once(self, ctx: PollContext, state: BridgeState) -> BridgeState:
        pruned = await self._prune_deleted_sessions(ctx.client, state=state)
        if pruned is not state:
            state = pruned
        for rollout_path in iter_codex_rollout_paths(self._codex_home):
            tracked = await self._ensure_tracked_rollout(
                ctx.client,
                state=state,
                rollout_path=rollout_path,
            )
            if tracked is None:
                continue
            previous = state.threads.get(tracked_state_key(tracked))
            synced = await self._sync_tracked_rollout(ctx.client, tracked=tracked)
            if previous != synced:
                state.threads[tracked_state_key(synced)] = synced
        return state

    async def prune_deleted_sessions(
        self,
        client: httpx.AsyncClient,
        *,
        state: BridgeState,
    ) -> BridgeState:
        return await self._prune_deleted_sessions(client, state=state)

    async def _prune_deleted_sessions(
        self,
        client: httpx.AsyncClient,
        *,
        state: BridgeState,
    ) -> BridgeState:
        if not state.threads:
            return state
        remaining = dict(state.threads)
        changed = False
        for state_key, tracked in list(state.threads.items()):
            if tracked.ssh_alias is not None:
                continue
            if active_codex_rollout_path(self._codex_home, tracked.thread_id) is not None:
                continue
            rollout_path = Path(tracked.rollout_path)
            if rollout_path.is_file():
                continue
            try:
                await delete_omnigent_session(client, session_id=tracked.session_id)
            except httpx.HTTPError:
                _logger.warning(
                    "Failed to delete Omnigent session %s for removed Codex thread %s",
                    tracked.session_id,
                    tracked.thread_id,
                    exc_info=True,
                )
                continue
            remaining.pop(state_key, None)
            changed = True
            _logger.info(
                "Deleted Omnigent session %s after Codex removed thread %s",
                tracked.session_id,
                tracked.thread_id,
            )
        if not changed:
            return state
        return BridgeState(threads=remaining)

    async def _ensure_tracked_rollout(
        self,
        client: httpx.AsyncClient,
        *,
        state: BridgeState,
        rollout_path: Path,
    ) -> TrackedRollout | None:
        thread_id = thread_id_from_rollout_path(rollout_path)
        if thread_id is None:
            return None
        existing = state.threads.get(thread_id)
        if existing is not None:
            if existing.rollout_path != str(rollout_path):
                existing = TrackedRollout(
                    thread_id=existing.thread_id,
                    rollout_path=str(rollout_path),
                    session_id=existing.session_id,
                    byte_offset=existing.byte_offset,
                    turn_id=existing.turn_id,
                    workspace=existing.workspace,
                    connection_id=existing.connection_id,
                    ssh_alias=existing.ssh_alias,
                )
                state.threads[thread_id] = existing
            return existing

        rollout_mtime_ms = int(rollout_path.stat().st_mtime * 1000)
        if not rollout_is_recent(rollout_mtime_ms):
            return None

        try:
            imported = load_codex_session(thread_id, codex_home=self._codex_home)
        except SessionImportNotFoundError:
            return None

        session_id = await import_codex_session(
            client,
            thread_id=thread_id,
            workspace=imported.workspace,
            items=imported.items,
            rollout_path=str(rollout_path),
            byte_offset=rollout_path.stat().st_size,
            connection_id=None,
        )
        if session_id is None:
            return None
        tracked = TrackedRollout(
            thread_id=thread_id,
            rollout_path=str(rollout_path),
            session_id=session_id,
            byte_offset=rollout_path.stat().st_size,
            turn_id="history",
            workspace=imported.workspace,
        )
        state.threads[thread_id] = tracked
        _logger.info(
            "Imported standalone Codex session %s as Omnigent session %s",
            thread_id,
            session_id,
        )
        return tracked

    async def _sync_tracked_rollout(
        self,
        client: httpx.AsyncClient,
        *,
        tracked: TrackedRollout,
    ) -> TrackedRollout:
        rollout_path = Path(tracked.rollout_path)
        if not rollout_path.is_file():
            return tracked

        read_result = read_codex_rollout_from_offset(
            rollout_path,
            byte_offset=tracked.byte_offset,
            turn_id=tracked.turn_id,
            workspace=tracked.workspace,
        )
        if not read_result.items and read_result.byte_offset == tracked.byte_offset:
            return tracked

        await post_ambient_codex_sync(
            client,
            tracked=tracked,
            items=read_result.items,
            byte_offset=read_result.byte_offset,
            turn_id=read_result.turn_id,
        )

        return TrackedRollout(
            thread_id=tracked.thread_id,
            rollout_path=tracked.rollout_path,
            session_id=tracked.session_id,
            byte_offset=read_result.byte_offset,
            turn_id=read_result.turn_id,
            workspace=read_result.workspace or tracked.workspace,
            connection_id=tracked.connection_id,
            ssh_alias=tracked.ssh_alias,
        )
