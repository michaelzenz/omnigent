"""Configuration for Cursor ambient pollers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from omnigent.host.identity import CONFIG_PATH

_CLI_ENV_VAR = "OMNIGENT_CURSOR_CLI_AMBIENT_SYNC"
_IDE_ENV_VAR = "OMNIGENT_CURSOR_IDE_AMBIENT_SYNC"
_DEFAULT_POLL_INTERVAL_S = 3.0
_DEFAULT_REMOTE_INTERVAL_S = 3.0
_DEFAULT_REMOTE_BACKOFF_CAP_S = 30.0


@dataclass(frozen=True)
class CursorPollerConfig:
    """Resolved Cursor ambient poller settings."""

    enabled: bool
    interval_s: float
    remote_interval_s: float
    remote_backoff_cap_s: float


def _parse_enabled(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return None


def _load_section(config_path: Path, section_name: str) -> CursorPollerConfig:
    enabled: bool | None = None
    interval_s = _DEFAULT_POLL_INTERVAL_S
    remote_interval_s = _DEFAULT_REMOTE_INTERVAL_S
    remote_backoff_cap_s = _DEFAULT_REMOTE_BACKOFF_CAP_S
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
                    section = polling_section.get(section_name)
                    if isinstance(section, dict):
                        configured_enabled = _parse_enabled(section.get("enabled"))
                        if configured_enabled is not None:
                            enabled = configured_enabled
                        configured_interval = section.get("interval_s")
                        if isinstance(configured_interval, (int, float)) and configured_interval > 0:
                            interval_s = float(configured_interval)
                        configured_remote_interval = section.get("remote_interval_s")
                        if (
                            isinstance(configured_remote_interval, (int, float))
                            and configured_remote_interval > 0
                        ):
                            remote_interval_s = float(configured_remote_interval)
                        configured_backoff_cap = section.get("remote_backoff_cap_s")
                        if (
                            isinstance(configured_backoff_cap, (int, float))
                            and configured_backoff_cap > 0
                        ):
                            remote_backoff_cap_s = float(configured_backoff_cap)
    return CursorPollerConfig(
        enabled=enabled if enabled is not None else False,
        interval_s=interval_s,
        remote_interval_s=remote_interval_s,
        remote_backoff_cap_s=remote_backoff_cap_s,
    )


def load_cursor_cli_poller_config(config_path: Path = CONFIG_PATH) -> CursorPollerConfig:
    env_value = os.environ.get(_CLI_ENV_VAR)
    if env_value is not None:
        enabled = env_value.strip().lower() not in {"0", "false", "no", "off"}
        return CursorPollerConfig(
            enabled=enabled,
            interval_s=_DEFAULT_POLL_INTERVAL_S,
            remote_interval_s=_DEFAULT_REMOTE_INTERVAL_S,
            remote_backoff_cap_s=_DEFAULT_REMOTE_BACKOFF_CAP_S,
        )
    return _load_section(config_path, "cursor_cli")


def load_cursor_ide_poller_config(config_path: Path = CONFIG_PATH) -> CursorPollerConfig:
    env_value = os.environ.get(_IDE_ENV_VAR)
    if env_value is not None:
        enabled = env_value.strip().lower() not in {"0", "false", "no", "off"}
        return CursorPollerConfig(
            enabled=enabled,
            interval_s=_DEFAULT_POLL_INTERVAL_S,
            remote_interval_s=_DEFAULT_REMOTE_INTERVAL_S,
            remote_backoff_cap_s=_DEFAULT_REMOTE_BACKOFF_CAP_S,
        )
    return _load_section(config_path, "cursor_ide")


def cursor_cli_ambient_sync_enabled(config_path: Path = CONFIG_PATH) -> bool:
    return load_cursor_cli_poller_config(config_path).enabled


def cursor_ide_ambient_sync_enabled(config_path: Path = CONFIG_PATH) -> bool:
    return load_cursor_ide_poller_config(config_path).enabled
