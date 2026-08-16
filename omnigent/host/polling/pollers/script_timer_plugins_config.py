"""Defaults and per-plugin config for agent-authored timer plugins."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from omnigent.host.identity import CONFIG_PATH
from omnigent.host.polling.singleton_gate import parse_singleton_config
from omnigent.host.polling.timer_plugins_paths import (
    PLUGIN_CONFIG_NAME,
    PLUGIN_STATE_NAME,
)

_DEFAULT_TIMEOUT_S = 120.0
_DEFAULT_TICK_S = 30.0


@dataclass(frozen=True)
class ScriptTimerPluginsDefaults:
    """Host-wide defaults when a timer plugin omits ``config.yaml`` fields."""

    default_timeout_s: float
    tick_s: float


@dataclass(frozen=True)
class TimerPluginConfig:
    """Resolved schedule for one timer plugin folder."""

    fire_at: float | None
    timeout_s: float
    singleton: bool = False
    bound_role: str | None = None


@dataclass(frozen=True)
class TimerPluginState:
    """Last-fired marker written by the host after each invocation."""

    fired_at: float


def _positive_float(value: object) -> float | None:
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


def _finite_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        try:
            import math

            if math.isfinite(value):
                return float(value)
        except (TypeError, ValueError):
            return None
    return None


def load_script_timer_plugins_defaults(
    config_path: Path = CONFIG_PATH,
) -> ScriptTimerPluginsDefaults:
    """Load optional host-wide timer-plugin defaults from ``~/.omnigent/config.yaml``."""
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
                    plugins_section = polling_section.get("timer_plugins")
                    if isinstance(plugins_section, dict):
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
    return ScriptTimerPluginsDefaults(
        default_timeout_s=default_timeout_s,
        tick_s=tick_s,
    )


def load_timer_plugin_config(
    plugin_dir: Path,
    defaults: ScriptTimerPluginsDefaults,
) -> TimerPluginConfig:
    """Load ``config.yaml`` from one timer plugin folder, falling back to host defaults."""
    fire_at: float | None = None
    timeout_s = defaults.default_timeout_s
    config_path = plugin_dir / PLUGIN_CONFIG_NAME
    cfg: object = {}
    if config_path.is_file():
        try:
            with config_path.open(encoding="utf-8") as handle:
                cfg = yaml.safe_load(handle) or {}
        except OSError:
            cfg = {}
        if isinstance(cfg, dict):
            configured_fire_at = _finite_float(cfg.get("fire_at"))
            if configured_fire_at is not None:
                fire_at = configured_fire_at
            configured_timeout = _positive_float(cfg.get("timeout_s"))
            if configured_timeout is not None:
                timeout_s = configured_timeout
    singleton_cfg = parse_singleton_config(cfg)
    return TimerPluginConfig(
        fire_at=fire_at,
        timeout_s=timeout_s,
        singleton=singleton_cfg.singleton,
        bound_role=singleton_cfg.bound_role,
    )


def load_timer_plugin_state(plugin_dir: Path) -> TimerPluginState:
    """Load ``state.yaml`` written by the host after each fire (default 0)."""
    state_path = plugin_dir / PLUGIN_STATE_NAME
    if not state_path.is_file():
        return TimerPluginState(fired_at=0.0)
    try:
        with state_path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except OSError:
        return TimerPluginState(fired_at=0.0)
    if not isinstance(data, dict):
        return TimerPluginState(fired_at=0.0)
    fired_at = _finite_float(data.get("fired_at"))
    if fired_at is None:
        fired_at = 0.0
    return TimerPluginState(fired_at=fired_at)


def write_timer_plugin_state(plugin_dir: Path, *, fired_at: float) -> None:
    """Persist ``fired_at`` so the host does not re-fire the same ``fire_at``."""
    state_path = plugin_dir / PLUGIN_STATE_NAME
    state_path.write_text(yaml.safe_dump({"fired_at": fired_at}, sort_keys=False))
