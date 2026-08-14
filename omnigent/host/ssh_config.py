"""SSH pool configuration for the host daemon."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from omnigent.host.identity import CONFIG_PATH

_DEFAULT_MAX_CONCURRENT_COMMANDS = 1


@dataclass(frozen=True)
class SshPoolConfig:
    """Resolved SSH pool settings."""

    max_concurrent_commands: int


def load_ssh_pool_config(config_path: Path = CONFIG_PATH) -> SshPoolConfig:
    """Load SSH pool settings from ``~/.omnigent/config.yaml``."""
    max_concurrent = _DEFAULT_MAX_CONCURRENT_COMMANDS
    if config_path.exists():
        try:
            with config_path.open(encoding="utf-8") as handle:
                cfg = yaml.safe_load(handle) or {}
        except OSError:
            cfg = {}
        if isinstance(cfg, dict):
            host_section = cfg.get("host")
            if isinstance(host_section, dict):
                ssh_section = host_section.get("ssh")
                if isinstance(ssh_section, dict):
                    configured = ssh_section.get("max_concurrent_commands")
                    if isinstance(configured, int) and configured > 0:
                        max_concurrent = configured
    return SshPoolConfig(max_concurrent_commands=max_concurrent)
