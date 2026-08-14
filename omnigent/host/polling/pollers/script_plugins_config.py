"""Defaults and per-plugin config for agent-authored poll plugins."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from omnigent.host.identity import CONFIG_PATH
from omnigent.host.polling.poll_plugins_paths import PLUGIN_CONFIG_NAME

_DEFAULT_INTERVAL_S = 60.0
_DEFAULT_TIMEOUT_S = 120.0
_DEFAULT_TICK_S = 5.0


@dataclass(frozen=True)
class ScriptPollPluginsDefaults:
    """Host-wide defaults when a plugin omits ``config.yaml`` fields."""

    default_interval_s: float
    default_timeout_s: float
    tick_s: float


@dataclass(frozen=True)
class PluginPollConfig:
    """Resolved schedule for one plugin folder."""

    interval_s: float
    timeout_s: float


def _positive_float(value: object) -> float | None:
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


def load_script_poll_plugins_defaults(
    config_path: Path = CONFIG_PATH,
) -> ScriptPollPluginsDefaults:
    """Load optional host-wide poll-plugin defaults from ``~/.omnigent/config.yaml``."""
    default_interval_s = _DEFAULT_INTERVAL_S
    default_timeout_s = _DEFAULT_TIMEOUT_S
    tick_s = _DEFAULT_TICK_S
    if config_path.exists():
        try:
            with config_path.open(encoding="utf-8") as handle:
                cfg = yaml.safe_load(handle) or {}
        except OSError:
            cfg = {}
        if isinstance(cfg, dict):
            host_section = cfg.get("host")
            if isinstance(host_section, dict):
                polling_section = host_section.get("polling")
                if isinstance(polling_section, dict):
                    plugins_section = polling_section.get("poll_plugins")
                    if isinstance(plugins_section, dict):
                        configured_default_interval = _positive_float(
                            plugins_section.get("default_interval_s")
                        )
                        if configured_default_interval is None:
                            configured_default_interval = _positive_float(
                                plugins_section.get("interval_s")
                            )
                        if configured_default_interval is not None:
                            default_interval_s = configured_default_interval
                        configured_default_timeout = _positive_float(
                            plugins_section.get("default_timeout_s")
                        )
                        if configured_default_timeout is None:
                            configured_default_timeout = _positive_float(
                                plugins_section.get("timeout_s")
                            )
                        if configured_default_timeout is not None:
                            default_timeout_s = configured_default_timeout
                        configured_tick = _positive_float(plugins_section.get("tick_s"))
                        if configured_tick is not None:
                            tick_s = configured_tick
    return ScriptPollPluginsDefaults(
        default_interval_s=default_interval_s,
        default_timeout_s=default_timeout_s,
        tick_s=tick_s,
    )


def load_plugin_poll_config(
    plugin_dir: Path,
    defaults: ScriptPollPluginsDefaults,
) -> PluginPollConfig:
    """Load ``config.yaml`` from one plugin folder, falling back to host defaults."""
    interval_s = defaults.default_interval_s
    timeout_s = defaults.default_timeout_s
    config_path = plugin_dir / PLUGIN_CONFIG_NAME
    if config_path.is_file():
        try:
            with config_path.open(encoding="utf-8") as handle:
                cfg = yaml.safe_load(handle) or {}
        except OSError:
            cfg = {}
        if isinstance(cfg, dict):
            configured_interval = _positive_float(cfg.get("interval_s"))
            if configured_interval is not None:
                interval_s = configured_interval
            configured_timeout = _positive_float(cfg.get("timeout_s"))
            if configured_timeout is not None:
                timeout_s = configured_timeout
    return PluginPollConfig(interval_s=interval_s, timeout_s=timeout_s)
