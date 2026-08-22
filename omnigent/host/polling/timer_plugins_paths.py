"""Paths for agent-authored host timer plugins.

Shares the inclusive two-root scan with poll plugins (see
:mod:`omnigent.host.polling.poll_plugins_paths`): the data dir plus the
configured ``host.puppygarden.root``, deduped by name (data dir wins).
"""

from __future__ import annotations

from pathlib import Path

from omnigent.host.identity import CONFIG_PATH
from omnigent.host.polling.poll_plugins_paths import (
    merge_plugin_dirs,
    scan_dir,
    scan_roots,
)

TIMER_PLUGINS_DIRNAME = "timer_plugins"
RUN_SCRIPT_NAME = "run.py"
PLUGIN_CONFIG_NAME = "config.yaml"
PLUGIN_STATE_NAME = "state.yaml"
README_NAME = "README.md"


def timer_scan_roots(config_path: Path = CONFIG_PATH) -> list[Path]:
    """Roots scanned for timer plugins (data dir, then puppygarden if set)."""
    return scan_roots(config_path, TIMER_PLUGINS_DIRNAME)


def iter_timer_plugin_dirs(
    root: Path | None = None,
    *,
    config_path: Path = CONFIG_PATH,
) -> list[Path]:
    """Return timer plugin directories that contain a ``run.py`` entry point.

    With ``root`` set, scans that single directory (used by tests). With
    ``root`` unset, scans both the data dir and the configured puppygarden
    root inclusively, deduping by name (data dir wins).
    """
    return iter_timer_plugin_dirs_with_collisions(root, config_path=config_path)[0]


def iter_timer_plugin_dirs_with_collisions(
    root: Path | None = None,
    *,
    config_path: Path = CONFIG_PATH,
) -> tuple[list[Path], set[str]]:
    """Like :func:`iter_timer_plugin_dirs` but also returns colliding names.

    Returns ``(plugin_dirs, duplicate_names)``. See
    :func:`omnigent.host.polling.poll_plugins_paths.iter_plugin_dirs_with_collisions`.
    """
    if root is not None:
        return scan_dir(root), set()
    return merge_plugin_dirs(timer_scan_roots(config_path))
