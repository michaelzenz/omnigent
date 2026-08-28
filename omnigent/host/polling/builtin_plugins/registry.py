"""Registry of built-in poll plugins.

Built-in plugins run in-process (not via subprocess) and their config/state
lives in ``<data_dir>/poll_plugins/<name>/``. The registry is populated at
import time and is immutable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class BuiltinPlugin:
    """A built-in poll plugin that runs in-process."""

    name: str
    """Unique plugin name, also used as the config/state subdirectory."""

    main: Callable[[], None]
    """Entry point — called on each poll tick. Reads config from env/paths."""

    default_config: dict[str, Any]
    """Default config.yaml contents written on first run."""

    description: str
    """Short human-readable description shown in the UI."""


_REGISTRY: list[BuiltinPlugin] = []


def register_builtin_plugin(plugin: BuiltinPlugin) -> None:
    """Register a built-in plugin."""
    _REGISTRY.append(plugin)


def list_builtin_plugins() -> list[BuiltinPlugin]:
    """Return all registered built-in plugins."""
    return list(_REGISTRY)


def get_builtin_plugin(name: str) -> BuiltinPlugin | None:
    """Return a built-in plugin by name, or None."""
    for plugin in _REGISTRY:
        if plugin.name == name:
            return plugin
    return None


def _register_all() -> None:
    """Register all built-in plugins at import time."""
    from omnigent.host.polling.builtin_plugins.external_session_watcher import (
        main as external_session_watcher_main,
    )

    register_builtin_plugin(
        BuiltinPlugin(
            name="external_session_watcher",
            main=external_session_watcher_main,
            default_config={
                "enabled": True,
                "interval_s": 60,
                "singleton": True,
                "bound_role": "secretary",
                "scan_dirs": ["~/.codex/sessions", "~/.cursor/projects"],
                "snippet_lines": 50,
                "recency_window_s": 86400,
                "sink_time_s": 180,
            },
            description="Discovers and tracks external sessions (Cursor, Codex, etc.)",
        )
    )
    register_builtin_plugin(
        BuiltinPlugin(
            name="session_watcher",
            main=lambda: None,  # no-op — toggle only
            default_config={
                "enabled": True,
                "interval_s": 300,
                "singleton": True,
                "bound_role": "secretary",
            },
            description="Internal session watcher — auto-adopts omnigent sessions and emits turn-finish events",
        )
    )


_register_all()
