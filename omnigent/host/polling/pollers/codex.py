"""Codex ambient rollout poller."""

from __future__ import annotations

from pathlib import Path

from omnigent.host.identity import CONFIG_PATH
from omnigent.host.polling.context import PollContext
from omnigent.host.polling.pollers.codex_config import load_codex_poller_config
from omnigent.host.polling.pollers.codex_local import CodexLocalSubPoller
from omnigent.host.polling.pollers.codex_remote import CodexRemoteSubPoller
from omnigent.host.polling.pollers.codex_state import BridgeState, hydrate_bridge_state
from omnigent.session_import.codex_rollout import default_codex_home
from omnigent.ssh_connections_store import read_ssh_connections


class CodexAmbientPoller:
    """Mirror standalone and remote Codex rollouts into Omnigent."""

    def __init__(
        self,
        *,
        codex_home: Path | None = None,
        config_path: Path = CONFIG_PATH,
        poll_interval_s: float | None = None,
    ) -> None:
        self._codex_home = codex_home
        self._config_path = config_path
        self._poll_interval_s = poll_interval_s
        self._state: BridgeState | None = None
        self._local: CodexLocalSubPoller | None = None
        self._remotes: list[CodexRemoteSubPoller] = []

    @property
    def name(self) -> str:
        return "codex"

    def _config(self):
        return load_codex_poller_config(self._config_path)

    def enabled(self, ctx: PollContext) -> bool:
        return self._config().enabled

    def interval_s(self, ctx: PollContext) -> float:
        if self._poll_interval_s is not None:
            return self._poll_interval_s
        return self._config().interval_s

    def _codex_home_path(self) -> Path:
        if self._codex_home is None:
            self._codex_home = default_codex_home()
        return self._codex_home

    def _sync_remote_subpollers(self) -> None:
        profiles = [profile for profile in read_ssh_connections() if profile.codex_remote]
        by_id = {poller.profile.id: poller for poller in self._remotes}
        self._remotes = [by_id.get(profile.id, CodexRemoteSubPoller(profile)) for profile in profiles]

    async def on_start(self, ctx: PollContext) -> None:
        self._state = await hydrate_bridge_state(ctx.client, host_id=ctx.host_id)
        self._local = CodexLocalSubPoller(codex_home=self._codex_home_path())
        self._sync_remote_subpollers()

    async def poll_once(self, ctx: PollContext) -> None:
        if self._state is None:
            self._state = BridgeState(threads={})
        if self._local is None:
            self._local = CodexLocalSubPoller(codex_home=self._codex_home_path())
        self._sync_remote_subpollers()
        self._state = await self._local.poll_once(ctx, self._state)
        for remote in self._remotes:
            self._state = await remote.poll_once(ctx, self._state)

    async def on_stop(self) -> None:
        self._state = None
        self._local = None
        self._remotes = []
