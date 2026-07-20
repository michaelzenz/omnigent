"""Configuration for the Codex ambient poller."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from omnigent.host.identity import CONFIG_PATH

_ENV_VAR = "OMNIGENT_CODEX_AMBIENT_SYNC"
_LEGACY_CONFIG_KEY = "codex_ambient_sync"
_DEFAULT_POLL_INTERVAL_S = 3.0
_DEFAULT_REMOTE_INTERVAL_S = 3.0
_DEFAULT_REMOTE_BACKOFF_CAP_S = 30.0


@dataclass(frozen=True)
class CodexPollerConfig:
    """Resolved Codex ambient poller settings."""

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


def load_codex_poller_config(config_path: Path = CONFIG_PATH) -> CodexPollerConfig:
    """Load Codex poller settings from env and ``~/.omnigent/config.yaml``."""
    env_value = os.environ.get(_ENV_VAR)
    if env_value is not None:
        enabled = env_value.strip().lower() not in {"0", "false", "no", "off"}
        return CodexPollerConfig(
            enabled=enabled,
            interval_s=_DEFAULT_POLL_INTERVAL_S,
            remote_interval_s=_DEFAULT_REMOTE_INTERVAL_S,
            remote_backoff_cap_s=_DEFAULT_REMOTE_BACKOFF_CAP_S,
        )

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
                    codex_section = polling_section.get("codex")
                    if isinstance(codex_section, dict):
                        configured_enabled = _parse_enabled(codex_section.get("enabled"))
                        if configured_enabled is not None:
                            enabled = configured_enabled
                        configured_interval = codex_section.get("interval_s")
                        if isinstance(configured_interval, (int, float)) and configured_interval > 0:
                            interval_s = float(configured_interval)
                        configured_remote_interval = codex_section.get("remote_interval_s")
                        if (
                            isinstance(configured_remote_interval, (int, float))
                            and configured_remote_interval > 0
                        ):
                            remote_interval_s = float(configured_remote_interval)
                        configured_backoff_cap = codex_section.get("remote_backoff_cap_s")
                        if isinstance(configured_backoff_cap, (int, float)) and configured_backoff_cap > 0:
                            remote_backoff_cap_s = float(configured_backoff_cap)
                legacy_enabled = _parse_enabled(host_section.get(_LEGACY_CONFIG_KEY))
                if enabled is None and legacy_enabled is not None:
                    enabled = legacy_enabled

    if enabled is None:
        enabled = True
    return CodexPollerConfig(
        enabled=enabled,
        interval_s=interval_s,
        remote_interval_s=remote_interval_s,
        remote_backoff_cap_s=remote_backoff_cap_s,
    )


def codex_ambient_sync_enabled(config_path: Path = CONFIG_PATH) -> bool:
    """Return whether the host daemon should mirror standalone Codex sessions."""
    return load_codex_poller_config(config_path).enabled
