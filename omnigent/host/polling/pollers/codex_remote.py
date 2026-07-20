"""Remote SSH Codex rollout polling."""

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
    read_codex_rollout_from_offset,
    thread_id_from_rollout_path,
)
from omnigent.session_import.local import load_codex_session_from_rollout
from omnigent.session_import.models import SessionImportNotFoundError
from omnigent.ssh_connections_store import SshConnectionProfile
from omnigent.ssh_remote import (
    ssh_remote_active_codex_rollout,
    ssh_remote_codex_rollouts,
    ssh_remote_file_size,
    ssh_remote_path_exists,
    ssh_remote_rollout_to_tempfile,
)

_logger = logging.getLogger(__name__)


class CodexRemoteSubPoller:
    """Scan Codex rollouts on one configured SSH host."""

    def __init__(self, profile: SshConnectionProfile) -> None:
        self._profile = profile

    @property
    def profile(self) -> SshConnectionProfile:
        return self._profile

    async def poll_once(self, ctx: PollContext, state: BridgeState) -> BridgeState:
        pruned = await self._prune_deleted_sessions(ctx.client, state=state)
        if pruned is not state:
            state = pruned
        try:
            rollouts = await ssh_remote_codex_rollouts(self._profile)
        except OSError:
            _logger.warning(
                "Failed to list remote Codex rollouts via %s",
                self._profile.alias,
                exc_info=True,
            )
            return state
        for rollout in rollouts:
            tracked = await self._ensure_tracked_rollout(
                ctx.client,
                state=state,
                remote_path=rollout.path,
                rollout_mtime_ms=rollout.mtime_ms,
            )
            if tracked is None:
                continue
            previous = state.threads.get(tracked_state_key(tracked))
            synced = await self._sync_tracked_rollout(ctx.client, tracked=tracked)
            if previous != synced:
                state.threads[tracked_state_key(synced)] = synced
        return state

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
            if tracked.ssh_alias != self._profile.alias:
                continue
            active = await ssh_remote_active_codex_rollout(self._profile, tracked.thread_id)
            if active is not None:
                continue
            if await ssh_remote_path_exists(self._profile, tracked.rollout_path):
                continue
            try:
                await delete_omnigent_session(client, session_id=tracked.session_id)
            except httpx.HTTPError:
                _logger.warning(
                    "Failed to delete Omnigent session %s for removed remote Codex thread %s",
                    tracked.session_id,
                    tracked.thread_id,
                    exc_info=True,
                )
                continue
            remaining.pop(state_key, None)
            changed = True
        if not changed:
            return state
        return BridgeState(threads=remaining)

    async def _ensure_tracked_rollout(
        self,
        client: httpx.AsyncClient,
        *,
        state: BridgeState,
        remote_path: str,
        rollout_mtime_ms: int,
    ) -> TrackedRollout | None:
        thread_id = thread_id_from_rollout_path(Path(remote_path))
        if thread_id is None:
            return None
        state_key = f"{self._profile.alias}:{thread_id}"
        existing = state.threads.get(state_key)
        if existing is not None:
            if existing.rollout_path != remote_path:
                existing = TrackedRollout(
                    thread_id=existing.thread_id,
                    rollout_path=remote_path,
                    session_id=existing.session_id,
                    byte_offset=existing.byte_offset,
                    turn_id=existing.turn_id,
                    workspace=existing.workspace,
                    connection_id=self._profile.id,
                    ssh_alias=self._profile.alias,
                )
                state.threads[state_key] = existing
            return existing

        if not rollout_is_recent(rollout_mtime_ms):
            return None

        temp_path: Path | None = None
        try:
            temp_path = await ssh_remote_rollout_to_tempfile(self._profile, remote_path)
            imported = load_codex_session_from_rollout(temp_path, thread_id)
            remote_size = await ssh_remote_file_size(self._profile, remote_path)
        except (OSError, SessionImportNotFoundError):
            return None
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

        session_id = await import_codex_session(
            client,
            thread_id=thread_id,
            workspace=imported.workspace,
            items=imported.items,
            rollout_path=remote_path,
            byte_offset=remote_size,
            connection_id=self._profile.id,
            ssh_alias=self._profile.alias,
        )
        if session_id is None:
            return None
        tracked = TrackedRollout(
            thread_id=thread_id,
            rollout_path=remote_path,
            session_id=session_id,
            byte_offset=remote_size,
            turn_id="history",
            workspace=imported.workspace,
            connection_id=self._profile.id,
            ssh_alias=self._profile.alias,
        )
        state.threads[state_key] = tracked
        _logger.info(
            "Imported remote Codex session %s via %s as Omnigent session %s",
            thread_id,
            self._profile.alias,
            session_id,
        )
        return tracked

    async def _sync_tracked_rollout(
        self,
        client: httpx.AsyncClient,
        *,
        tracked: TrackedRollout,
    ) -> TrackedRollout:
        temp_path: Path | None = None
        try:
            temp_path = await ssh_remote_rollout_to_tempfile(
                self._profile,
                tracked.rollout_path,
                byte_offset=tracked.byte_offset,
            )
            read_result = read_codex_rollout_from_offset(
                temp_path,
                byte_offset=0,
                turn_id=tracked.turn_id,
                workspace=tracked.workspace,
            )
        except OSError:
            return tracked
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

        if not read_result.items and read_result.byte_offset == 0:
            return tracked

        new_offset = tracked.byte_offset + read_result.byte_offset
        await post_ambient_codex_sync(
            client,
            tracked=tracked,
            items=read_result.items,
            byte_offset=new_offset,
            turn_id=read_result.turn_id,
        )

        return TrackedRollout(
            thread_id=tracked.thread_id,
            rollout_path=tracked.rollout_path,
            session_id=tracked.session_id,
            byte_offset=new_offset,
            turn_id=read_result.turn_id,
            workspace=read_result.workspace or tracked.workspace,
            connection_id=tracked.connection_id,
            ssh_alias=tracked.ssh_alias,
        )
