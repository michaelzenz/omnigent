"""Run agent-authored poll plugins from ``<data_dir>/poll_plugins/*/run.py``."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from omnigent.host.identity import CONFIG_PATH
from omnigent.host.polling.context import PollContext
from omnigent.host.polling.poll_plugins_paths import RUN_SCRIPT_NAME, iter_plugin_dirs
from omnigent.host.polling.pollers.script_plugins_config import (
    load_script_poll_plugins_config,
)
from omnigent.process_logging import data_dir

_logger = logging.getLogger(__name__)


class ScriptPollPluginsPoller:
    """Execute each plugin folder's ``run.py`` on a fixed interval."""

    read_only = False

    def __init__(self, *, config_path: Path = CONFIG_PATH) -> None:
        self._config_path = config_path

    @property
    def name(self) -> str:
        return "poll_plugins"

    def enabled(self, ctx: PollContext) -> bool:
        return load_script_poll_plugins_config(self._config_path).enabled

    def interval_s(self, ctx: PollContext) -> float:
        return load_script_poll_plugins_config(self._config_path).interval_s

    async def on_start(self, ctx: PollContext) -> None:
        return None

    async def on_stop(self) -> None:
        return None

    async def poll_once(self, ctx: PollContext) -> None:
        config = load_script_poll_plugins_config(self._config_path)
        plugin_dirs = iter_plugin_dirs()
        if not plugin_dirs:
            return
        for plugin_dir in plugin_dirs:
            await self._run_plugin(plugin_dir, ctx=ctx, timeout_s=config.timeout_s)

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
