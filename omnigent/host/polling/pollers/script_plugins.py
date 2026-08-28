"""Run poll plugins — directory-discovered (subprocess) and built-in (in-process)."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

import yaml

from omnigent.host.identity import CONFIG_PATH
from omnigent.host.polling.builtin_plugins.registry import (
    BuiltinPlugin,
    list_builtin_plugins,
)
from omnigent.host.polling.context import PollContext
from omnigent.host.polling.plugin_health import PluginHealthTracker
from omnigent.host.polling.poll_plugins_paths import (
    README_NAME,
    RUN_SCRIPT_NAME,
    iter_plugin_dirs_with_collisions,
)
from omnigent.host.polling.pollers.script_plugins_config import (
    PluginPollConfig,
    PluginPollConfigError,
    load_plugin_poll_config,
    load_script_poll_plugins_defaults,
    write_plugin_poll_enabled,
)
from omnigent.host.polling.singleton_gate import (
    RoleHostResolver,
    SingletonConfig,
    SingletonConfigError,
    should_run_singleton,
)
from omnigent.process_logging import data_dir

_logger = logging.getLogger(__name__)


class ScriptPollPluginsPoller:
    """Execute poll plugins on their configured intervals.

    Merges two sources:
    - Directory-discovered plugins (``<data_dir>/poll_plugins/*/run.py``) — run as subprocess.
    - Built-in plugins (registered in ``builtin_plugins.registry``) — run in-process.
    """

    read_only = False

    def __init__(self, *, config_path: Path = CONFIG_PATH) -> None:
        self._config_path = config_path
        self._last_run: dict[str, float] = {}
        self._resolver: RoleHostResolver | None = None
        self._health = PluginHealthTracker(kind="poll")

    @property
    def name(self) -> str:
        return "poll_plugins"

    def enabled(self, ctx: PollContext) -> bool:  # noqa: ARG002
        return True

    def interval_s(self, ctx: PollContext) -> float:  # noqa: ARG002
        return load_script_poll_plugins_defaults(self._config_path).tick_s

    async def on_start(self, ctx: PollContext) -> None:
        self._last_run = {}
        self._resolver = RoleHostResolver(ctx.client)

    async def on_stop(self) -> None:
        self._last_run = {}
        self._resolver = None

    async def poll_once(self, ctx: PollContext) -> None:
        defaults = load_script_poll_plugins_defaults(self._config_path)
        plugin_dirs, duplicates = iter_plugin_dirs_with_collisions(config_path=self._config_path)
        self._health.set_warnings(
            duplicates,
            "duplicate plugin name exists in both ~/.omnigent and puppygarden; "
            "using the local copy",
        )

        # Collect built-in plugin names so we can skip directory plugins with the same name.
        builtin_names = {p.name for p in list_builtin_plugins()}

        now = time.monotonic()

        # Run built-in plugins first.
        for builtin in list_builtin_plugins():
            plugin_dir = data_dir() / "poll_plugins" / builtin.name
            plugin_dir.mkdir(parents=True, exist_ok=True)
            self._ensure_builtin_config(builtin, plugin_dir)
            try:
                plugin_config = load_plugin_poll_config(plugin_dir, defaults)
            except (PluginPollConfigError, SingletonConfigError) as exc:
                _logger.warning("Built-in plugin %s skipped — invalid config: %s", builtin.name, exc)
                self._health.record_config_skip(builtin.name, error=str(exc))
                continue
            if not plugin_config.enabled:
                self._last_run.pop(builtin.name, None)
                self._health.record_disabled(builtin.name, interval_s=plugin_config.interval_s)
                continue
            last_run = self._last_run.get(builtin.name, 0.0)
            if now - last_run < plugin_config.interval_s:
                continue
            if plugin_config.singleton and self._resolver is not None:
                if not await should_run_singleton(
                    self._resolver,
                    SingletonConfig(
                        singleton=plugin_config.singleton,
                        bound_role=plugin_config.bound_role,
                    ),
                    host_id=ctx.host_id,
                ):
                    self._health.record_singleton_skip(builtin.name, interval_s=plugin_config.interval_s)
                    continue
            await self._run_builtin_plugin(builtin, ctx=ctx, interval_s=plugin_config.interval_s)
            self._last_run[builtin.name] = now

        # Run directory-discovered plugins (skip names that are built-in).
        for plugin_dir in plugin_dirs:
            if plugin_dir.name in builtin_names:
                continue
            if not (plugin_dir / README_NAME).is_file():
                _logger.warning(
                    "Poll plugin %s missing %s — skipping.",
                    plugin_dir.name,
                    README_NAME,
                )
                continue
            try:
                plugin_config = load_plugin_poll_config(plugin_dir, defaults)
            except (PluginPollConfigError, SingletonConfigError) as exc:
                _logger.warning("Poll plugin %s skipped — invalid config: %s", plugin_dir.name, exc)
                self._health.record_config_skip(plugin_dir.name, error=str(exc))
                continue
            if not plugin_config.enabled:
                self._last_run.pop(plugin_dir.name, None)
                self._health.record_disabled(plugin_dir.name, interval_s=plugin_config.interval_s)
                continue
            last_run = self._last_run.get(plugin_dir.name, 0.0)
            if now - last_run < plugin_config.interval_s:
                continue
            if plugin_config.singleton and self._resolver is not None:
                if not await should_run_singleton(
                    self._resolver,
                    SingletonConfig(
                        singleton=plugin_config.singleton,
                        bound_role=plugin_config.bound_role,
                    ),
                    host_id=ctx.host_id,
                ):
                    self._health.record_singleton_skip(plugin_dir.name, interval_s=plugin_config.interval_s)
                    continue
            await self._run_subprocess_plugin(plugin_dir, ctx=ctx, timeout_s=plugin_config.timeout_s, interval_s=plugin_config.interval_s)
            self._last_run[plugin_dir.name] = now

        await self._health.maybe_post(ctx)

    def _ensure_builtin_config(self, builtin: BuiltinPlugin, plugin_dir: Path) -> None:
        """Write default config.yaml if it doesn't exist for a built-in plugin."""
        config_path = plugin_dir / "config.yaml"
        if not config_path.exists():
            config_path.write_text(yaml.safe_dump(builtin.default_config, sort_keys=False))

    async def _run_builtin_plugin(
        self,
        builtin: BuiltinPlugin,
        *,
        ctx: PollContext,
        interval_s: float,
    ) -> None:
        """Run a built-in plugin's main() in-process with a timeout."""
        plugin_dir = data_dir() / "poll_plugins" / builtin.name
        env = {
            **os.environ,
            "OMNIGENT_SERVER_URL": ctx.server_url,
            "OMNIGENT_HOST_ID": ctx.host_id,
            "OMNIGENT_PLUGIN_DIR": str(plugin_dir.resolve()),
            "OMNIGENT_PLUGIN_NAME": builtin.name,
            "OMNIGENT_DATA_DIR": str(data_dir()),
        }
        old_env = dict(os.environ)
        os.environ.update(env)
        try:
            await asyncio.wait_for(
                asyncio.to_thread(builtin.main),
                timeout=120.0,
            )
        except TimeoutError:
            _logger.warning("Built-in plugin %s timed out", builtin.name)
            self._health.record_run(builtin.name, outcome="timeout", error="timed out", interval_s=interval_s)
        except Exception as exc:
            _logger.warning("Built-in plugin %s failed: %s", builtin.name, exc)
            self._health.record_run(builtin.name, outcome="exit_nonzero", error=str(exc), interval_s=interval_s)
        else:
            self._health.record_run(builtin.name, outcome="ok", interval_s=interval_s)
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    async def _run_subprocess_plugin(
        self,
        plugin_dir: Path,
        *,
        ctx: PollContext,
        timeout_s: float,
        interval_s: float,
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
            _logger.warning("Failed to start poll plugin %s", plugin_dir.name, exc_info=True)
            self._health.record_run(plugin_dir.name, outcome="start_failed", error="failed to start subprocess", interval_s=interval_s)
            return
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            _logger.warning("Poll plugin %s timed out after %.0fs", plugin_dir.name, timeout_s)
            self._health.record_run(plugin_dir.name, outcome="timeout", error=f"timed out after {timeout_s:.0f}s", interval_s=interval_s)
            return
        if proc.returncode != 0:
            detail = (stderr or stdout).decode(errors="replace").strip()
            _logger.warning("Poll plugin %s exited %s%s", plugin_dir.name, proc.returncode, f": {detail}" if detail else "")
            self._health.record_run(plugin_dir.name, outcome="exit_nonzero", error=detail or f"exit {proc.returncode}", interval_s=interval_s)
            return
        self._health.record_run(plugin_dir.name, outcome="ok", interval_s=interval_s)
