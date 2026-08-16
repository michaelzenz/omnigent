"""Paths for agent-authored host poll plugins."""

from __future__ import annotations

from pathlib import Path

from omnigent.process_logging import data_dir

POLL_PLUGINS_DIRNAME = "poll_plugins"
RUN_SCRIPT_NAME = "run.py"
PLUGIN_CONFIG_NAME = "config.yaml"
README_NAME = "README.md"


def poll_plugins_root() -> Path:
    """Return the directory containing one folder per poll plugin."""
    return data_dir() / POLL_PLUGINS_DIRNAME


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
