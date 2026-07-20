"""Cursor projects ambient transcript poller.

Reads ``~/.cursor/projects/*/agent-transcripts/*/*.jsonl`` — Cursor's
per-project transcript log. Cursor writes the same session UUID here for
both IDE and CLI chats, so one poller covers both without duplicating the
older ``~/.cursor/chats/`` SQLite store.
"""

from __future__ import annotations

from pathlib import Path

from omnigent.host.identity import CONFIG_PATH
from omnigent.host.polling.context import PollContext
from omnigent.host.polling.pollers.base import AmbientPoller, AmbientPollerConfig
from omnigent.host.polling.pollers.ambient_state import (
    AmbientBridgeState,
    apply_bridge_delta,
    hydrate_ambient_bridge_state,
    merge_bridge_deltas,
)
from omnigent.host.polling.pollers.cursor_config import load_cursor_projects_poller_config
from omnigent.host.polling.pollers.cursor_projects_local import CursorProjectsLocalSubPoller
from omnigent.host.polling.pollers.cursor_projects_remote import CursorProjectsRemoteSubPoller
from omnigent.ssh_connections_store import SshConnectionProfile


class CursorProjectsAmbientPoller(AmbientPoller):
    """Mirror local and remote ``~/.cursor/projects`` transcripts into Omnigent."""

    read_only = True

    def __init__(
        self,
        *,
        projects_root: Path | None = None,
        config_path: Path = CONFIG_PATH,
        poll_interval_s: float | None = None,
        read_only: bool | None = None,
    ) -> None:
        super().__init__(
            config_path=config_path,
            poll_interval_s=poll_interval_s,
            read_only=read_only,
        )
        self._projects_root = projects_root

    @property
    def name(self) -> str:
        return "cursor-projects"

    def _load_config(self) -> AmbientPollerConfig:
        config = load_cursor_projects_poller_config(self._config_path)
        return AmbientPollerConfig(
            enabled=config.enabled,
            interval_s=config.interval_s,
            remote_interval_s=config.remote_interval_s,
            remote_backoff_cap_s=config.remote_backoff_cap_s,
        )

    def _remote_profile_enabled(self, profile: SshConnectionProfile) -> bool:
        return profile.cursor_remote

    async def _hydrate_state(self, ctx: PollContext) -> AmbientBridgeState:
        return await hydrate_ambient_bridge_state(
            ctx.client,
            host_id=ctx.host_id,
            import_source="cursor-projects",
        )

    def _empty_state(self) -> AmbientBridgeState:
        return AmbientBridgeState(tracks={})

    def _create_local_subpoller(self) -> CursorProjectsLocalSubPoller:
        return CursorProjectsLocalSubPoller(projects_root=self._projects_root)

    def _create_remote_subpoller(
        self,
        profile: SshConnectionProfile,
        *,
        interval_s: float,
        backoff_cap_s: float,
    ) -> CursorProjectsRemoteSubPoller:
        return CursorProjectsRemoteSubPoller(
            profile,
            interval_s=interval_s,
            backoff_cap_s=backoff_cap_s,
        )

    def _merge_remote_deltas(self, state: AmbientBridgeState, deltas: list) -> AmbientBridgeState:
        return apply_bridge_delta(state, merge_bridge_deltas(*deltas))

    def _remote_failure_label(self) -> str:
        return "Cursor projects"
