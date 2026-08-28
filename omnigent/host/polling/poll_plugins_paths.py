"""Paths for agent-authored host poll plugins.

The host scans two roots inclusively and merges the results:

1. ``<data_dir>/poll_plugins`` (``~/.omnigent/poll_plugins`` or
   ``$OMNIGENT_DATA_DIR/poll_plugins``) — the user's local/runtime plugins.
2. ``<puppygarden_root>/poll_plugins`` — the shared, version-controlled
   plugins from a repo checkout, when ``host.puppygarden.root`` is set in the
   host config.

On a name collision the local (data-dir) plugin wins; the puppygarden copy is
ignored for that name. This lets a user override a repo plugin with a local
edit without forking the repo.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from omnigent.host.identity import CONFIG_PATH
from omnigent.process_logging import data_dir

POLL_PLUGINS_DIRNAME = "poll_plugins"
RUN_SCRIPT_NAME = "run.py"
PLUGIN_CONFIG_NAME = "config.yaml"
README_NAME = "README.md"


def resolve_puppygarden_root(config_path: Path = CONFIG_PATH) -> Path | None:
    """Read ``host.puppygarden.root`` from the host config, if set.

    Points the poller at a version-controlled ``puppygarden/`` directory (a
    cloned repo's ``puppygarden/``) so its ``poll_plugins/`` are scanned
    alongside the runtime data dir.
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
    puppygarden = host_section.get("puppygarden")
    if not isinstance(puppygarden, dict):
        return None
    raw = puppygarden.get("root")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return Path(raw).expanduser()


def scan_dir(base: Path) -> list[Path]:
    """Return plugin directories under ``base`` that contain a ``run.py``."""
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


def scan_roots(config_path: Path, section: str) -> list[Path]:
    """Ordered roots to scan for a plugin section (data dir first, puppygarden second)."""
    roots = [data_dir() / section]
    puppygarden = resolve_puppygarden_root(config_path)
    if puppygarden is not None:
        roots.append(puppygarden / section)
    return roots


def merge_plugin_dirs(roots: list[Path]) -> tuple[list[Path], set[str]]:
    """Scan each root in order and merge, deduping by plugin folder name.

    The first root to surface a given name wins (so the data-dir root, which
    is listed first, overrides a same-named puppygarden plugin). Returns the
    deduped directories and the set of names that appeared in more than one
    root (so callers can warn about the collision).
    """
    seen: set[str] = set()
    duplicates: set[str] = set()
    out: list[Path] = []
    for base in roots:
        for child in scan_dir(base):
            if child.name in seen:
                duplicates.add(child.name)
                continue
            seen.add(child.name)
            out.append(child)
    return out, duplicates


def plugin_scan_roots(config_path: Path = CONFIG_PATH) -> list[Path]:
    """Roots scanned for poll plugins (data dir, then puppygarden if set)."""
    return scan_roots(config_path, POLL_PLUGINS_DIRNAME)


def iter_plugin_dirs(
    root: Path | None = None,
    *,
    config_path: Path = CONFIG_PATH,
) -> list[Path]:
    """Return poll plugin directories that contain a ``run.py`` entry point.

    With ``root`` set, scans that single directory (used by tests). With
    ``root`` unset, scans both the data dir and the configured puppygarden
    root inclusively, deduping by name (data dir wins).
    """
    return iter_plugin_dirs_with_collisions(root, config_path=config_path)[0]


def iter_plugin_dirs_with_collisions(
    root: Path | None = None,
    *,
    config_path: Path = CONFIG_PATH,
) -> tuple[list[Path], set[str]]:
    """Like :func:`iter_plugin_dirs` but also returns colliding plugin names.

    Returns ``(plugin_dirs, duplicate_names)`` where ``duplicate_names`` is
    the set of names that exist in more than one scanned root (the data-dir
    copy wins; the others are dropped). Callers can surface a warning for
    these names. With ``root`` set (single dir) the duplicate set is empty.
    """
    if root is not None:
        return scan_dir(root), set()
    return merge_plugin_dirs(plugin_scan_roots(config_path))
