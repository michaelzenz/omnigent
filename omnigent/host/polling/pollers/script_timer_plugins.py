"""Run agent-authored timer plugins from ``<data_dir>/timer_plugins/*/run.py``.

Unlike poll plugins (which fire on a fixed interval), timer plugins fire once
at a wall-clock ``fire_at`` declared in ``config.yaml``. After firing, the host
writes ``state.yaml`` so the same ``fire_at`` is never re-fired — even across
restarts. To re-arm, ``run.py`` writes a new future ``fire_at`` to
``config.yaml`` before exiting.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

from omnigent.host.identity import CONFIG_PATH
from omnigent.host.polling.context import PollContext
from omnigent.host.polling.pollers.script_timer_plugins_config import (
    load_script_timer_plugins_defaults,
    load_timer_plugin_config,
    load_timer_plugin_state,
    write_timer_plugin_state,
)
from omnigent.host.polling.singleton_gate import (
    RoleHostResolver,
    SingletonConfig,
    SingletonConfigError,
    should_run_singleton,
)
from omnigent.host.polling.timer_plugins_paths import (
    README_NAME,
    RUN_SCRIPT_NAME,
    iter_timer_plugin_dirs,
    resolve_timer_plugins_root,
)
from omnigent.process_logging import data_dir

_logger = logging.getLogger(__name__)


class ScriptTimerPluginsPoller:
    """Execute each timer plugin folder's ``run.py`` when its ``fire_at`` is due."""

    read_only = False

    def __init__(self, *, config_path: Path = CONFIG_PATH) -> None:
        self._config_path = config_path
        self._resolver: RoleHostResolver | None = None

    @property
    def name(self) -> str:
        return "timer_plugins"

    def enabled(self, ctx: PollContext) -> bool:  # noqa: ARG002 -- PollSource interface; always enabled
        return True

    def interval_s(self, ctx: PollContext) -> float:  # noqa: ARG002 -- PollSource interface; interval from config
        return load_script_timer_plugins_defaults(self._config_path).tick_s

    async def on_start(self, ctx: PollContext) -> None:
        self._resolver = RoleHostResolver(ctx.client)

    async def on_stop(self) -> None:
        self._resolver = None

    async def poll_once(self, ctx: PollContext) -> None:
        defaults = load_script_timer_plugins_defaults(self._config_path)
        plugin_dirs = iter_timer_plugin_dirs(resolve_timer_plugins_root(self._config_path))
        if not plugin_dirs:
            return
        now = time.time()
        for plugin_dir in plugin_dirs:
            if not (plugin_dir / README_NAME).is_file():
                _logger.warning(
                    "Timer plugin %s missing %s — skipping. Every plugin MUST ship a "
                    "README.md describing its purpose, state shape, and edit notes; "
                    "agents editing this plugin must read it first.",
                    plugin_dir.name,
                    README_NAME,
                )
                continue
            try:
                cfg = load_timer_plugin_config(plugin_dir, defaults)
            except SingletonConfigError as exc:
                _logger.warning(
                    "Timer plugin %s skipped — invalid singleton config: %s",
                    plugin_dir.name,
                    exc,
                )
                continue
            if cfg.fire_at is None:
                continue
            state = load_timer_plugin_state(plugin_dir)
            if now < cfg.fire_at:
                continue
            if state.fired_at >= cfg.fire_at:
                continue
            if cfg.singleton and self._resolver is not None:
                # Singleton timers fire only on the host pinned to the bound
                # role. Same sticky-pin semantics as poll plugins.
                if not await should_run_singleton(
                    self._resolver,
                    SingletonConfig(singleton=cfg.singleton, bound_role=cfg.bound_role),
                    host_id=ctx.host_id,
                ):
                    continue
            await self._run_plugin(
                plugin_dir,
                ctx=ctx,
                fire_at=cfg.fire_at,
                timeout_s=cfg.timeout_s,
            )
            # Mark fired regardless of subprocess exit code — no retry on failure.
            write_timer_plugin_state(plugin_dir, fired_at=now)

    async def _run_plugin(
        self,
        plugin_dir: Path,
        *,
        ctx: PollContext,
        fire_at: float,
        timeout_s: float,
    ) -> None:
        run_py = plugin_dir / RUN_SCRIPT_NAME
        env = {
            **os.environ,
            "OMNIGENT_SERVER_URL": ctx.server_url,
            "OMNIGENT_HOST_ID": ctx.host_id,
            "OMNIGENT_PLUGIN_DIR": str(plugin_dir.resolve()),
            "OMNIGENT_PLUGIN_NAME": plugin_dir.name,
            "OMNIGENT_DATA_DIR": str(data_dir()),
        }
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(run_py),
                cwd=str(plugin_dir),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            _logger.warning(
                "Failed to start timer plugin %s",
                plugin_dir.name,
                exc_info=True,
            )
            await self._post_fire_failed(
                ctx,
                plugin_name=plugin_dir.name,
                fire_at=fire_at,
                reason="start_failed",
            )
            return
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            _logger.warning(
                "Timer plugin %s timed out after %.0fs",
                plugin_dir.name,
                timeout_s,
            )
            await self._post_fire_failed(
                ctx,
                plugin_name=plugin_dir.name,
                fire_at=fire_at,
                reason="timeout",
            )
            return
        if proc.returncode != 0:
            detail = (stderr or stdout).decode(errors="replace").strip()
            _logger.warning(
                "Timer plugin %s exited %s%s",
                plugin_dir.name,
                proc.returncode,
                f": {detail}" if detail else "",
            )
            await self._post_fire_failed(
                ctx,
                plugin_name=plugin_dir.name,
                fire_at=fire_at,
                reason="exit_nonzero",
                exit_code=proc.returncode,
                detail=detail,
            )

    async def _post_fire_failed(
        self,
        ctx: PollContext,
        *,
        plugin_name: str,
        fire_at: float,
        reason: str,
        exit_code: int | None = None,
        detail: str = "",
    ) -> None:
        """Emit a ``timer.fire_failed`` task event when a plugin invocation fails."""
        payload: dict[str, object] = {
            "plugin": plugin_name,
            "fire_at": int(fire_at),
            "reason": reason,
        }
        if exit_code is not None:
            payload["exit_code"] = exit_code
        if detail:
            payload["detail"] = detail[:2000]
        summary = f"timer:{plugin_name} fire_failed reason:{reason} fire_at:{int(fire_at)}"
        if exit_code is not None:
            summary += f" exit:{exit_code}"
        body = {
            "event_type": "timer.fire_failed",
            "title": f"Timer plugin {plugin_name} failed to fire",
            "summary": summary,
            "source": f"timer_plugin:{plugin_name}",
            "source_key": str(int(fire_at)),
            "source_offset": 1,
            "payload": payload,
        }
        try:
            response = await ctx.client.post("/v1/task-events", json=body)
            response.raise_for_status()
        except Exception:  # noqa: BLE001 -- best-effort fire_failed post; never crash the poller
            _logger.warning(
                "Failed to post timer.fire_failed for plugin %s",
                plugin_name,
                exc_info=True,
            )
