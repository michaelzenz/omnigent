"""Remote SSH Codex rollout polling."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

from omnigent.host.polling.context import PollContext
from omnigent.host.polling.pollers.codex_state import (
    BridgeState,
    BridgeStateDelta,
    TrackedRollout,
    apply_bridge_delta,
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
    RemoteCodexRollout,
    ssh_remote_codex_rollouts,
    ssh_remote_missing_rollout_thread_ids,
    ssh_remote_rollout_to_tempfile,
)

_logger = logging.getLogger(__name__)


class CodexRemoteSubPoller:
    """Scan Codex rollouts on one configured SSH host."""

    def __init__(
        self,
        profile: SshConnectionProfile,
        *,
        interval_s: float,
        backoff_cap_s: float,
    ) -> None:
        self._profile = profile
        self._interval_s = interval_s
        self._backoff_cap_s = backoff_cap_s
        self._last_poll_at: float | None = None
        self._backoff_s = 0.0
        self._consecutive_failures = 0
        self._import_conflicts: set[str] = set()

    @property
    def profile(self) -> SshConnectionProfile:
        return self._profile

    def is_due(self, now: float) -> bool:
        """Return whether this remote host should be polled at *now*."""
        if self._last_poll_at is None:
            return True
        return now - self._last_poll_at >= max(self._interval_s, self._backoff_s)

    def record_outcome(self, now: float, *, success: bool) -> None:
        """Update per-remote backoff after one poll attempt."""
        self._last_poll_at = now
        if success:
            self._consecutive_failures = 0
            self._backoff_s = 0.0
            return
        self._consecutive_failures += 1
        self._backoff_s = min(
            self._backoff_cap_s,
            self._interval_s * (2 ** min(self._consecutive_failures, 5)),
        )

    async def poll_once(self, ctx: PollContext, state: BridgeState) -> BridgeState:
        """Compatibility wrapper that mutates *state* in place."""
        try:
            delta = await self.poll_once_delta(ctx, state)
        except OSError:
            self.record_outcome(time.monotonic(), success=False)
            return state
        self.record_outcome(time.monotonic(), success=True)
        return apply_bridge_delta(state, delta)

    async def poll_once_delta(self, ctx: PollContext, state: BridgeState) -> BridgeStateDelta:
        """Scan one SSH host and return thread updates without mutating *state*."""
        updated: dict[str, TrackedRollout] = {}
        removed: set[str] = set()
        removed.update(await self._prune_deleted_sessions(ctx.client, state=state))
        try:
            rollouts = await ssh_remote_codex_rollouts(self._profile)
        except OSError:
            _logger.warning(
                "Failed to list remote Codex rollouts via %s",
                self._profile.alias,
                exc_info=True,
            )
            raise
        for rollout in rollouts:
            tracked = await self._ensure_tracked_rollout(
                ctx.client,
                state=state,
                rollout=rollout,
                updated=updated,
            )
            if tracked is None:
                continue
            previous = state.threads.get(tracked_state_key(tracked))
            if previous is None:
                previous = updated.get(tracked_state_key(tracked))
            synced = await self._sync_tracked_rollout(ctx.client, tracked=tracked)
            if previous != synced:
                updated[tracked_state_key(synced)] = synced
        return BridgeStateDelta(updated=updated, removed=removed)

    async def _prune_deleted_sessions(
        self,
        client: httpx.AsyncClient,
        *,
        state: BridgeState,
    ) -> set[str]:
        entries: list[tuple[str, str, str]] = []
        for state_key, tracked in state.threads.items():
            if tracked.ssh_alias != self._profile.alias:
                continue
            entries.append((state_key, tracked.thread_id, tracked.rollout_path))
        if not entries:
            return set()
        try:
            missing_thread_ids = await ssh_remote_missing_rollout_thread_ids(
                self._profile,
                [(thread_id, rollout_path) for _, thread_id, rollout_path in entries],
            )
        except OSError:
            _logger.warning(
                "Failed to batch-check removed remote Codex threads via %s",
                self._profile.alias,
                exc_info=True,
            )
            return set()
        removed: set[str] = set()
        for state_key, thread_id, _rollout_path in entries:
            if thread_id not in missing_thread_ids:
                continue
            tracked = state.threads[state_key]
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
            removed.add(state_key)
        return removed

    async def _ensure_tracked_rollout(
        self,
        client: httpx.AsyncClient,
        *,
        state: BridgeState,
        rollout: RemoteCodexRollout,
        updated: dict[str, TrackedRollout],
    ) -> TrackedRollout | None:
        remote_path = rollout.path
        thread_id = thread_id_from_rollout_path(Path(remote_path))
        if thread_id is None:
            return None
        state_key = f"{self._profile.alias}:{thread_id}"
        if state_key in self._import_conflicts:
            return None
        existing = state.threads.get(state_key)
        if existing is None:
            existing = updated.get(state_key)
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
                updated[state_key] = existing
            return existing

        if not rollout_is_recent(rollout.mtime_ms):
            return None

        temp_path: Path | None = None
        try:
            temp_path = await ssh_remote_rollout_to_tempfile(self._profile, remote_path)
            imported = load_codex_session_from_rollout(temp_path, thread_id)
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
            byte_offset=rollout.size,
            connection_id=self._profile.id,
            ssh_alias=self._profile.alias,
        )
        if session_id is None:
            self._import_conflicts.add(state_key)
            return None
        tracked = TrackedRollout(
            thread_id=thread_id,
            rollout_path=remote_path,
            session_id=session_id,
            byte_offset=rollout.size,
            turn_id="history",
            workspace=imported.workspace,
            connection_id=self._profile.id,
            ssh_alias=self._profile.alias,
        )
        updated[state_key] = tracked
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
