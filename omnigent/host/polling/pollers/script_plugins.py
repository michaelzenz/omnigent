"""Run agent-authored poll plugins from ``<data_dir>/poll_plugins/*/run.py``."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

from omnigent.host.identity import CONFIG_PATH
from omnigent.host.polling.context import PollContext
from omnigent.host.polling.poll_plugins_paths import RUN_SCRIPT_NAME, iter_plugin_dirs
from omnigent.host.polling.pollers.script_plugins_config import (
    load_plugin_poll_config,
    load_script_poll_plugins_defaults,
)
from omnigent.process_logging import data_dir

_logger = logging.getLogger(__name__)


class ScriptPollPluginsPoller:
    """Execute each plugin folder's ``run.py`` on its configured interval."""

    read_only = False

    def __init__(self, *, config_path: Path = CONFIG_PATH) -> None:
        self._config_path = config_path
        self._last_run: dict[str, float] = {}

    @property
    def name(self) -> str:
        return "poll_plugins"

    def enabled(self, ctx: PollContext) -> bool:
        return True

    def interval_s(self, ctx: PollContext) -> float:
        return load_script_poll_plugins_defaults(self._config_path).tick_s

    async def on_start(self, ctx: PollContext) -> None:
        self._last_run = {}

    async def on_stop(self) -> None:
        self._last_run = {}

    async def poll_once(self, ctx: PollContext) -> None:
        defaults = load_script_poll_plugins_defaults(self._config_path)
        plugin_dirs = iter_plugin_dirs()
        if not plugin_dirs:
            return
        now = time.monotonic()
        for plugin_dir in plugin_dirs:
            plugin_config = load_plugin_poll_config(plugin_dir, defaults)
            last_run = self._last_run.get(plugin_dir.name, 0.0)
            if now - last_run < plugin_config.interval_s:
                continue
            await self._run_plugin(
                plugin_dir,
                ctx=ctx,
                timeout_s=plugin_config.timeout_s,
            )
            self._last_run[plugin_dir.name] = now

    async def _run_plugin(
        self,
        plugin_dir: Path,
        *,
        ctx: PollContext,
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
                "Failed to start poll plugin %s",
                plugin_dir.name,
                exc_info=True,
            )
            return
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            _logger.warning(
                "Poll plugin %s timed out after %.0fs",
                plugin_dir.name,
                timeout_s,
            )
            return
        if proc.returncode != 0:
            detail = (stderr or stdout).decode(errors="replace").strip()
            _logger.warning(
                "Poll plugin %s exited %s%s",
                plugin_dir.name,
                proc.returncode,
                f": {detail}" if detail else "",
            )
