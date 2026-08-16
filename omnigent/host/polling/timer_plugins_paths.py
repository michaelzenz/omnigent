"""Paths for agent-authored host timer plugins."""

from __future__ import annotations

from pathlib import Path

from omnigent.host.identity import CONFIG_PATH
from omnigent.host.polling.poll_plugins_paths import _configured_root
from omnigent.process_logging import data_dir

TIMER_PLUGINS_DIRNAME = "timer_plugins"
RUN_SCRIPT_NAME = "run.py"
PLUGIN_CONFIG_NAME = "config.yaml"
PLUGIN_STATE_NAME = "state.yaml"
README_NAME = "README.md"


def resolve_timer_plugins_root(config_path: Path = CONFIG_PATH) -> Path:
    """Return the directory containing one folder per timer plugin.

    Honors ``host.polling.timer_plugins.root`` in the host config; falls back to
    ``<data_dir>/timer_plugins`` (``~/.omnigent/timer_plugins`` or
    ``$OMNIGENT_DATA_DIR/timer_plugins``).
    """
    configured = _configured_root(config_path, TIMER_PLUGINS_DIRNAME)
    if configured is not None:
        return configured
    return data_dir() / TIMER_PLUGINS_DIRNAME


def timer_plugins_root() -> Path:
    """Return the directory containing one folder per timer plugin."""
    return resolve_timer_plugins_root()


def iter_timer_plugin_dirs(root: Path | None = None) -> list[Path]:
    """Return plugin directories that contain a ``run.py`` entry point."""
    base = root if root is not None else timer_plugins_root()
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
