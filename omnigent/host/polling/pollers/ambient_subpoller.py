"""Shared local and remote ambient sub-poller bases."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from omnigent.host.polling.context import PollContext
from omnigent.host.polling.pollers.ambient_state import (
    AmbientBridgeState,
    AmbientBridgeStateDelta,
    AmbientImportSource,
    TrackedAmbientSession,
    apply_bridge_delta,
    delete_omnigent_session,
    import_ambient_session,
    post_ambient_sync,
    replace_tracked,
    source_is_recent,
    tracked_state_key,
)
from omnigent.session_import.models import LocalSessionImport, SessionImportNotFoundError
from omnigent.ssh_connections_store import SshConnectionProfile

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AmbientReadResult:
    """Normalized incremental read from one external session."""

    items: tuple
    byte_offset: int
    turn_id: str | None = None
    workspace: str | None = None


class RemoteSubPollerBase(ABC):
    """Shared SSH backoff and poll-once-delta shell for ambient sources."""

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

    @abstractmethod
    def _source_label(self) -> str:
        """Short label for logs, e.g. ``"Codex"`` or ``"Cursor CLI"``."""

    def is_due(self, now: float) -> bool:
        if self._last_poll_at is None:
            return True
        return now - self._last_poll_at >= max(self._interval_s, self._backoff_s)

    def record_outcome(self, now: float, *, success: bool) -> None:
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

    async def poll_once(self, ctx: PollContext, state: AmbientBridgeState) -> AmbientBridgeState:
        """Compatibility wrapper that mutates *state* in place."""
        try:
            delta = await self.poll_once_delta(ctx, state)
        except OSError:
            self.record_outcome(time.monotonic(), success=False)
            return state
        self.record_outcome(time.monotonic(), success=True)
        return apply_bridge_delta(state, delta)

    async def poll_once_delta(self, ctx: PollContext, state: AmbientBridgeState) -> AmbientBridgeStateDelta:
        updated: dict[str, TrackedAmbientSession] = {}
        removed: set[str] = set()
        removed.update(await self._prune_deleted_sessions(ctx.client, state=state))
        try:
            discoveries = await self._list_remote_discoveries()
        except OSError:
            _logger.warning(
                "Failed to list remote %s sessions via %s",
                self._source_label(),
                self._profile.alias,
                exc_info=True,
            )
            raise
        for discovery in discoveries:
            tracked = await self._ensure_tracked(
                ctx.client,
                state=state,
                discovery=discovery,
                updated=updated,
            )
            if tracked is None:
                continue
            key = tracked_state_key(tracked)
            previous = state.tracks.get(key)
            if previous is None:
                previous = updated.get(key)
            synced = await self._sync_tracked(ctx.client, tracked=tracked, discovery=discovery)
            if previous != synced:
                updated[key] = synced
        return AmbientBridgeStateDelta(updated=updated, removed=removed)

    @abstractmethod
    async def _list_remote_discoveries(self) -> list[Any]: ...

    @abstractmethod
    async def _remote_source_exists(self, source_path: str) -> bool: ...

    @abstractmethod
    def _discovery_session_key(self, discovery: Any) -> str: ...

    @abstractmethod
    def _discovery_source_path(self, discovery: Any) -> str: ...

    @abstractmethod
    def _discovery_mtime_ms(self, discovery: Any) -> int: ...

    @abstractmethod
    def _import_source(self) -> AmbientImportSource: ...

    async def _load_import(self, discovery: Any) -> LocalSessionImport:
        raise NotImplementedError

    def _initial_byte_offset(self, discovery: Any, imported: LocalSessionImport) -> int:
        raise NotImplementedError

    async def _prepare_import(
        self,
        discovery: Any,
    ) -> tuple[LocalSessionImport, int] | None:
        try:
            imported = await self._load_import(discovery)
            return imported, self._initial_byte_offset(discovery, imported)
        except (SessionImportNotFoundError, OSError):
            return None

    @abstractmethod
    async def _read_updates(
        self,
        discovery: Any,
        *,
        tracked: TrackedAmbientSession,
    ) -> AmbientReadResult: ...

    async def _prune_deleted_sessions(
        self,
        client: httpx.AsyncClient,
        *,
        state: AmbientBridgeState,
    ) -> set[str]:
        removed: set[str] = set()
        for key, tracked in list(state.tracks.items()):
            if tracked.ssh_alias != self._profile.alias:
                continue
            if await self._remote_source_exists(tracked.source_path):
                continue
            try:
                await delete_omnigent_session(client, session_id=tracked.session_id)
            except httpx.HTTPError:
                _logger.warning(
                    "Failed to delete Omnigent session %s for removed remote %s session %s",
                    tracked.session_id,
                    self._source_label(),
                    tracked.session_key,
                    exc_info=True,
                )
                continue
            removed.add(key)
        return removed

    async def _ensure_tracked(
        self,
        client: httpx.AsyncClient,
        *,
        state: AmbientBridgeState,
        discovery: Any,
        updated: dict[str, TrackedAmbientSession],
    ) -> TrackedAmbientSession | None:
        session_key = self._discovery_session_key(discovery)
        source_path = self._discovery_source_path(discovery)
        placeholder = TrackedAmbientSession(
            session_key=session_key,
            source_path=source_path,
            session_id="",
            byte_offset=0,
            ssh_alias=self._profile.alias,
            import_source=self._import_source(),
        )
        key = tracked_state_key(placeholder)
        existing = state.tracks.get(key)
        if existing is None:
            existing = updated.get(key)
        if existing is not None:
            if existing.source_path != source_path:
                existing = replace_tracked(existing, source_path=source_path)
                updated[key] = existing
            return existing

        conflict_key = f"{self._profile.alias}:{session_key}"
        if conflict_key in self._import_conflicts:
            return None
        if not source_is_recent(self._discovery_mtime_ms(discovery)):
            return None

        prepared = await self._prepare_import(discovery)
        if prepared is None:
            return None
        imported, byte_offset = prepared

        session_id = await import_ambient_session(
            client,
            import_source=self._import_source(),
            session_key=session_key,
            workspace=imported.workspace,
            items=imported.items,
            source_path=source_path,
            byte_offset=byte_offset,
            connection_id=self._profile.id,
            ssh_alias=self._profile.alias,
        )
        if session_id is None:
            self._import_conflicts.add(conflict_key)
            return None
        tracked = TrackedAmbientSession(
            session_key=session_key,
            source_path=source_path,
            session_id=session_id,
            byte_offset=byte_offset,
            workspace=imported.workspace,
            connection_id=self._profile.id,
            ssh_alias=self._profile.alias,
            import_source=self._import_source(),
        )
        updated[key] = tracked
        return tracked

    async def _sync_tracked(
        self,
        client: httpx.AsyncClient,
        *,
        tracked: TrackedAmbientSession,
        discovery: Any,
    ) -> TrackedAmbientSession:
        try:
            read_result = await self._read_updates(discovery, tracked=tracked)
        except OSError:
            return tracked
        if not read_result.items and read_result.byte_offset == tracked.byte_offset:
            return tracked
        await post_ambient_sync(
            client,
            tracked=tracked,
            items=read_result.items,
            byte_offset=read_result.byte_offset,
            turn_id=read_result.turn_id or tracked.turn_id,
        )
        return replace_tracked(
            tracked,
            byte_offset=read_result.byte_offset,
            turn_id=read_result.turn_id or tracked.turn_id,
            workspace=read_result.workspace or tracked.workspace,
        )


class LocalAmbientSubPollerBase(ABC):
    """Shared local scan/import/sync loop for ambient sources."""

    def __init__(self) -> None:
        self._import_conflicts: set[str] = set()

    @abstractmethod
    def _source_label(self) -> str: ...

    @abstractmethod
    def _import_source(self) -> AmbientImportSource: ...

    @abstractmethod
    def _iter_discoveries(self) -> Iterator[Any]: ...

    @abstractmethod
    def _discovery_session_key(self, discovery: Any) -> str: ...

    @abstractmethod
    def _discovery_source_path(self, discovery: Any) -> str: ...

    @abstractmethod
    def _discovery_mtime_ms(self, discovery: Any) -> int: ...

    @abstractmethod
    async def _local_source_missing(self, tracked: TrackedAmbientSession) -> bool: ...

    @abstractmethod
    def _load_import(self, discovery: Any) -> LocalSessionImport: ...

    @abstractmethod
    def _initial_byte_offset(self, discovery: Any, imported: LocalSessionImport) -> int: ...

    @abstractmethod
    def _read_updates(
        self,
        tracked: TrackedAmbientSession,
    ) -> AmbientReadResult: ...

    async def poll_once(self, ctx: PollContext, state: AmbientBridgeState) -> AmbientBridgeState:
        pruned = await self._prune_deleted_sessions(ctx.client, state=state)
        if pruned is not state:
            state = pruned
        for discovery in self._iter_discoveries():
            tracked = await self._ensure_tracked(ctx.client, state=state, discovery=discovery)
            if tracked is None:
                continue
            key = tracked_state_key(tracked)
            previous = state.tracks.get(key)
            synced = await self._sync_tracked(ctx.client, tracked=tracked)
            if previous != synced:
                state.tracks[key] = synced
        return state

    async def prune_deleted_sessions(
        self,
        client: httpx.AsyncClient,
        *,
        state: AmbientBridgeState,
    ) -> AmbientBridgeState:
        return await self._prune_deleted_sessions(client, state=state)

    async def _prune_deleted_sessions(
        self,
        client: httpx.AsyncClient,
        *,
        state: AmbientBridgeState,
    ) -> AmbientBridgeState:
        if not state.tracks:
            return state
        remaining = dict(state.tracks)
        changed = False
        for key, tracked in list(state.tracks.items()):
            if tracked.ssh_alias is not None:
                continue
            if not await self._local_source_missing(tracked):
                continue
            try:
                await delete_omnigent_session(client, session_id=tracked.session_id)
            except httpx.HTTPError:
                _logger.warning(
                    "Failed to delete Omnigent session %s for removed %s session %s",
                    tracked.session_id,
                    self._source_label(),
                    tracked.session_key,
                    exc_info=True,
                )
                continue
            remaining.pop(key, None)
            changed = True
            _logger.info(
                "Deleted Omnigent session %s after %s removed session %s",
                tracked.session_id,
                self._source_label(),
                tracked.session_key,
            )
        if not changed:
            return state
        return AmbientBridgeState(tracks=remaining)

    async def _ensure_tracked(
        self,
        client: httpx.AsyncClient,
        *,
        state: AmbientBridgeState,
        discovery: Any,
    ) -> TrackedAmbientSession | None:
        session_key = self._discovery_session_key(discovery)
        source_path = self._discovery_source_path(discovery)
        key = session_key
        existing = state.tracks.get(key)
        if existing is not None:
            if existing.source_path != source_path:
                existing = replace_tracked(existing, source_path=source_path)
                state.tracks[key] = existing
            return existing

        if session_key in self._import_conflicts:
            return None
        if not source_is_recent(self._discovery_mtime_ms(discovery)):
            return None

        try:
            imported = self._load_import(discovery)
        except SessionImportNotFoundError:
            return None

        byte_offset = self._initial_byte_offset(discovery, imported)
        session_id = await import_ambient_session(
            client,
            import_source=self._import_source(),
            session_key=session_key,
            workspace=imported.workspace,
            items=imported.items,
            source_path=source_path,
            byte_offset=byte_offset,
            connection_id=None,
        )
        if session_id is None:
            self._import_conflicts.add(session_key)
            return None
        tracked = TrackedAmbientSession(
            session_key=session_key,
            source_path=source_path,
            session_id=session_id,
            byte_offset=byte_offset,
            workspace=imported.workspace,
            import_source=self._import_source(),
        )
        state.tracks[key] = tracked
        _logger.info(
            "Imported %s session %s as Omnigent session %s",
            self._source_label(),
            session_key,
            session_id,
        )
        return tracked

    async def _sync_tracked(
        self,
        client: httpx.AsyncClient,
        *,
        tracked: TrackedAmbientSession,
    ) -> TrackedAmbientSession:
        if not Path(tracked.source_path).is_file():
            return tracked
        read_result = self._read_updates(tracked)
        if not read_result.items and read_result.byte_offset == tracked.byte_offset:
            return tracked
        await post_ambient_sync(
            client,
            tracked=tracked,
            items=read_result.items,
            byte_offset=read_result.byte_offset,
            turn_id=read_result.turn_id or tracked.turn_id,
        )
        return replace_tracked(
            tracked,
            byte_offset=read_result.byte_offset,
            turn_id=read_result.turn_id or tracked.turn_id,
            workspace=read_result.workspace or tracked.workspace,
        )
