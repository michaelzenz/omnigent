"""Paths for agent-authored host poll plugins."""

from __future__ import annotations

from pathlib import Path

import yaml

from omnigent.host.identity import CONFIG_PATH
from omnigent.process_logging import data_dir

POLL_PLUGINS_DIRNAME = "poll_plugins"
RUN_SCRIPT_NAME = "run.py"
PLUGIN_CONFIG_NAME = "config.yaml"
README_NAME = "README.md"


def _configured_root(config_path: Path, section: str) -> Path | None:
    """Read ``host.polling.<section>.root`` from the host config, if set.

    Lets a from-source install point the poller at a version-controlled plugins
    directory (e.g. a cloned repo's ``puppygarden/poll_plugins``) instead of the
    runtime data dir, so edits take effect without a copy/sync step.
    """
    if not config_path.exists():
        return None
    try:
        with config_path.open(encoding="utf-8") as handle:
            cfg = yaml.safe_load(handle) or {}
    except OSError:
        return None
    if not isinstance(cfg, dict):
        return None
    host_section = cfg.get("host")
    if not isinstance(host_section, dict):
        return None
    polling_section = host_section.get("polling")
    if not isinstance(polling_section, dict):
        return None
    plugins_section = polling_section.get(section)
    if not isinstance(plugins_section, dict):
        return None
    raw = plugins_section.get("root")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return Path(raw).expanduser()


def resolve_poll_plugins_root(config_path: Path = CONFIG_PATH) -> Path:
    """Return the directory containing one folder per poll plugin.

    Honors ``host.polling.poll_plugins.root`` in the host config; falls back to
    ``<data_dir>/poll_plugins`` (``~/.omnigent/poll_plugins`` or
    ``$OMNIGENT_DATA_DIR/poll_plugins``).
    """
    configured = _configured_root(config_path, POLL_PLUGINS_DIRNAME)
    if configured is not None:
        return configured
    return data_dir() / POLL_PLUGINS_DIRNAME


def poll_plugins_root() -> Path:
    """Return the directory containing one folder per poll plugin."""
    return resolve_poll_plugins_root()


def iter_plugin_dirs(root: Path | None = None) -> list[Path]:
    """Return plugin directories that contain a ``run.py`` entry point."""
    base = root if root is not None else poll_plugins_root()
    if not base.is_dir():
        return []
    plugin_dirs: list[Path] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("."):
            continue
        if (child / RUN_SCRIPT_NAME).is_file():
            plugin_dirs.append(child)
    return plugin_dirs
