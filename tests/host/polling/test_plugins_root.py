"""Tests for configurable plugin root resolution (host.polling.<section>.root)."""

from __future__ import annotations

from pathlib import Path

from omnigent.host.polling.poll_plugins_paths import resolve_poll_plugins_root
from omnigent.host.polling.timer_plugins_paths import resolve_timer_plugins_root


def _write_config(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def test_poll_root_defaults_to_data_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "data"))
    cfg = tmp_path / "empty.yaml"
    cfg.write_text("")
    assert resolve_poll_plugins_root(cfg) == tmp_path / "data" / "poll_plugins"


def test_poll_root_uses_configured_root(tmp_path: Path) -> None:
    custom = tmp_path / "repo" / "plugins" / "poll_plugins"
    custom.mkdir(parents=True)
    cfg = tmp_path / "config.yaml"
    _write_config(
        cfg,
        "host:\n  polling:\n    poll_plugins:\n      root: " + str(custom) + "\n",
    )
    assert resolve_poll_plugins_root(cfg) == custom


def test_poll_root_expands_tilde(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / "config.yaml"
    _write_config(
        cfg,
        "host:\n  polling:\n    poll_plugins:\n      root: ~/myplugins\n",
    )
    assert resolve_poll_plugins_root(cfg) == tmp_path / "myplugins"


def test_poll_root_missing_config_file_falls_back(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "data"))
    assert resolve_poll_plugins_root(tmp_path / "nope.yaml") == (
        tmp_path / "data" / "poll_plugins"
    )


def test_poll_root_non_dict_root_ignored(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "data"))
    cfg = tmp_path / "config.yaml"
    _write_config(
        cfg,
        "host:\n  polling:\n    poll_plugins:\n      root: 123\n",
    )
    assert resolve_poll_plugins_root(cfg) == tmp_path / "data" / "poll_plugins"


def test_timer_root_defaults_to_data_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "data"))
    cfg = tmp_path / "empty.yaml"
    cfg.write_text("")
    assert resolve_timer_plugins_root(cfg) == tmp_path / "data" / "timer_plugins"


def test_timer_root_uses_configured_root(tmp_path: Path) -> None:
    custom = tmp_path / "repo" / "plugins" / "timer_plugins"
    custom.mkdir(parents=True)
    cfg = tmp_path / "config.yaml"
    _write_config(
        cfg,
        "host:\n  polling:\n    timer_plugins:\n      root: " + str(custom) + "\n",
    )
    assert resolve_timer_plugins_root(cfg) == custom


def test_timer_root_independent_of_poll_root(tmp_path: Path, monkeypatch) -> None:
    """Each section's root is resolved independently — setting one doesn't set the other."""
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "data"))
    poll_custom = tmp_path / "poll_custom"
    poll_custom.mkdir()
    cfg = tmp_path / "config.yaml"
    _write_config(
        cfg,
        "host:\n  polling:\n    poll_plugins:\n      root: " + str(poll_custom) + "\n",
    )
    assert resolve_poll_plugins_root(cfg) == poll_custom
    # timer_plugins has no root set → falls back to data dir.
    assert resolve_timer_plugins_root(cfg) == tmp_path / "data" / "timer_plugins"
