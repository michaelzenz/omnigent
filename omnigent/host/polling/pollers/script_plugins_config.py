"""Configuration for agent-authored poll plugins."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from omnigent.host.identity import CONFIG_PATH

_ENV_VAR = "OMNIGENT_POLL_PLUGINS"
_DEFAULT_POLL_INTERVAL_S = 60.0
_DEFAULT_TIMEOUT_S = 120.0


@dataclass(frozen=True)
class ScriptPollPluginsConfig:
    """Resolved poll-plugin poller settings."""

    enabled: bool
    interval_s: float
    timeout_s: float


def _parse_enabled(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return None


def load_script_poll_plugins_config(
    config_path: Path = CONFIG_PATH,
) -> ScriptPollPluginsConfig:
    """Load poll-plugin poller settings from env and ``~/.omnigent/config.yaml``."""
    env_value = os.environ.get(_ENV_VAR)
    if env_value is not None:
        enabled = env_value.strip().lower() not in {"0", "false", "no", "off"}
        return ScriptPollPluginsConfig(
            enabled=enabled,
            interval_s=_DEFAULT_POLL_INTERVAL_S,
            timeout_s=_DEFAULT_TIMEOUT_S,
        )

    enabled: bool | None = None
    interval_s = _DEFAULT_POLL_INTERVAL_S
    timeout_s = _DEFAULT_TIMEOUT_S
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
                        configured_enabled = _parse_enabled(plugins_section.get("enabled"))
                        if configured_enabled is not None:
                            enabled = configured_enabled
                        configured_interval = plugins_section.get("interval_s")
                        if isinstance(configured_interval, (int, float)) and configured_interval > 0:
                            interval_s = float(configured_interval)
                        configured_timeout = plugins_section.get("timeout_s")
                        if isinstance(configured_timeout, (int, float)) and configured_timeout > 0:
                            timeout_s = float(configured_timeout)

    if enabled is None:
        enabled = False
    return ScriptPollPluginsConfig(
        enabled=enabled,
        interval_s=interval_s,
        timeout_s=timeout_s,
    )


def script_poll_plugins_enabled(config_path: Path = CONFIG_PATH) -> bool:
    """Return whether the host should run agent-authored poll plugins."""
    return load_script_poll_plugins_config(config_path).enabled
