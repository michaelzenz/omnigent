"""Base class for host ambient source pollers."""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from omnigent.host.identity import CONFIG_PATH
from omnigent.host.polling.context import PollContext
from omnigent.ssh_connections_store import SshConnectionProfile, read_ssh_connections

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AmbientPollerConfig:
    """Shared config shape for ambient pollers."""

    enabled: bool
    interval_s: float
    remote_interval_s: float
    remote_backoff_cap_s: float


class RemoteSubPoller(Protocol):
    """One SSH host scanned by an ambient poller."""

    @property
    def profile(self) -> SshConnectionProfile: ...

    def is_due(self, now: float) -> bool: ...

    def record_outcome(self, now: float, *, success: bool) -> None: ...

    async def poll_once_delta(self, ctx: PollContext, state: Any) -> Any: ...


class AmbientPoller(ABC):
    """Local + remote ambient poller with shared scheduling and lifecycle.

    Subclasses implement source-specific discovery, import, and sync. The
    ``read_only`` flag marks one-way mirror pollers (Codex/Cursor today);
    bidirectional pollers can set it to ``False`` when outbound sync lands.
    """

    read_only: bool = True

    def __init__(
        self,
        *,
        config_path: Path = CONFIG_PATH,
        poll_interval_s: float | None = None,
        read_only: bool | None = None,
    ) -> None:
        self._config_path = config_path
        self._poll_interval_s = poll_interval_s
        if read_only is not None:
            self.read_only = read_only
        self._state: Any = None
        self._local: Any = None
        self._remotes: list[RemoteSubPoller] = []

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier used for logging and scheduler stats."""

    @abstractmethod
    def _load_config(self) -> AmbientPollerConfig:
        """Return resolved poller settings."""

    @abstractmethod
    def _remote_profile_enabled(self, profile: SshConnectionProfile) -> bool:
        """Return whether *profile* should be polled for this source."""

    @abstractmethod
    async def _hydrate_state(self, ctx: PollContext) -> Any:
        """Load server-owned track cursors for this host."""

    @abstractmethod
    def _empty_state(self) -> Any:
        """Return a cold in-memory state when hydration is unavailable."""

    @abstractmethod
    def _create_local_subpoller(self) -> Any:
        """Create the local filesystem sub-poller."""

    @abstractmethod
    def _create_remote_subpoller(
        self,
        profile: SshConnectionProfile,
        *,
        interval_s: float,
        backoff_cap_s: float,
    ) -> RemoteSubPoller:
        """Create one remote SSH sub-poller for *profile*."""

    @abstractmethod
    def _merge_remote_deltas(self, state: Any, deltas: list[Any]) -> Any:
        """Apply remote sub-poller deltas onto *state*."""

    @abstractmethod
    def _remote_failure_label(self) -> str:
        """Short source label for remote poll failure logs."""

    def enabled(self, ctx: PollContext) -> bool:
        return self._load_config().enabled

    def interval_s(self, ctx: PollContext) -> float:
        if self._poll_interval_s is not None:
            return self._poll_interval_s
        return self._load_config().interval_s

    def _sync_remote_subpollers(self) -> None:
        config = self._load_config()
        profiles = [
            profile for profile in read_ssh_connections() if self._remote_profile_enabled(profile)
        ]
        by_id = {poller.profile.id: poller for poller in self._remotes}
        self._remotes = [
            by_id.get(
                profile.id,
                self._create_remote_subpoller(
                    profile,
                    interval_s=config.remote_interval_s,
                    backoff_cap_s=config.remote_backoff_cap_s,
                ),
            )
            for profile in profiles
        ]

    async def on_start(self, ctx: PollContext) -> None:
        self._state = await self._hydrate_state(ctx)
        self._local = self._create_local_subpoller()
        self._sync_remote_subpollers()

    async def poll_once(self, ctx: PollContext) -> None:
        if self._state is None:
            self._state = self._empty_state()
        if self._local is None:
            self._local = self._create_local_subpoller()
        self._sync_remote_subpollers()
        self._state = await self._local.poll_once(ctx, self._state)

        now = time.monotonic()
        due_remotes = [remote for remote in self._remotes if remote.is_due(now)]
        if not due_remotes:
            return

        results = await asyncio.gather(
            *(remote.poll_once_delta(ctx, self._state) for remote in due_remotes),
            return_exceptions=True,
        )
        deltas = []
        for remote, result in zip(due_remotes, results, strict=True):
            if isinstance(result, Exception):
                remote.record_outcome(now, success=False)
                _logger.warning(
                    "Remote %s poll failed via %s",
                    self._remote_failure_label(),
                    remote.profile.alias,
                    exc_info=result,
                )
                continue
            remote.record_outcome(now, success=True)
            deltas.append(result)
        if deltas:
            self._state = self._merge_remote_deltas(self._state, deltas)

    async def on_stop(self) -> None:
        self._state = None
        self._local = None
        self._remotes = []
