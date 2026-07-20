"""Codex ambient rollout poller."""

from __future__ import annotations

from pathlib import Path

from omnigent.host.codex_ambient_bridge import (
    _DEFAULT_POLL_INTERVAL_S,
    _BridgeState,
    _hydrate_bridge_state,
    _poll_codex_ambient_once,
    codex_ambient_sync_enabled,
)
from omnigent.host.identity import CONFIG_PATH
from omnigent.host.polling.context import PollContext
from omnigent.session_import.codex_rollout import default_codex_home


class CodexAmbientPoller:
    """Mirror standalone and remote Codex rollouts into Omnigent."""

    def __init__(
        self,
        *,
        codex_home: Path | None = None,
        config_path: Path = CONFIG_PATH,
        poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    ) -> None:
        self._codex_home = codex_home
        self._config_path = config_path
        self._poll_interval_s = poll_interval_s
        self._state: _BridgeState | None = None

    @property
    def name(self) -> str:
        return "codex"

    def enabled(self, ctx: PollContext) -> bool:
        return codex_ambient_sync_enabled(self._config_path)

    def interval_s(self, ctx: PollContext) -> float:
        return self._poll_interval_s

    async def on_start(self, ctx: PollContext) -> None:
        self._state = await _hydrate_bridge_state(ctx.client, host_id=ctx.host_id)
        if self._codex_home is None:
            self._codex_home = default_codex_home()

    async def poll_once(self, ctx: PollContext) -> None:
        if self._state is None:
            self._state = _BridgeState(threads={})
        if self._codex_home is None:
            self._codex_home = default_codex_home()
        self._state = await _poll_codex_ambient_once(
            ctx.client,
            state=self._state,
            codex_home=self._codex_home,
        )

    async def on_stop(self) -> None:
        self._state = None
