"""Tests for inclusive plugin scanning (data dir + ``host.puppygarden.root``)."""

from __future__ import annotations

from pathlib import Path

from omnigent.host.polling.poll_plugins_paths import (
    iter_plugin_dirs,
    iter_plugin_dirs_with_collisions,
    plugin_scan_roots,
    resolve_puppygarden_root,
)
from omnigent.host.polling.timer_plugins_paths import (
    iter_timer_plugin_dirs,
    iter_timer_plugin_dirs_with_collisions,
    timer_scan_roots,
)


def _write_config(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def _make_plugin(parent: Path, name: str) -> Path:
    d = parent / name
    d.mkdir(parents=True)
    (d / "run.py").write_text("print('ok')\n")
    return d


def test_puppygarden_root_defaults_to_none(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "data"))
    cfg = tmp_path / "empty.yaml"
    cfg.write_text("")
    assert resolve_puppygarden_root(cfg) is None


def test_puppygarden_root_reads_host_puppygarden_root(tmp_path: Path) -> None:
    pg = tmp_path / "repo" / "puppygarden"
    pg.mkdir(parents=True)
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "host:\n  puppygarden:\n    root: " + str(pg) + "\n")
    assert resolve_puppygarden_root(cfg) == pg


def test_puppygarden_root_expands_tilde(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "host:\n  puppygarden:\n    root: ~/pg\n")
    assert resolve_puppygarden_root(cfg) == tmp_path / "pg"


def test_puppygarden_root_non_dict_ignored(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "data"))
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "host:\n  puppygarden:\n    root: 123\n")
    assert resolve_puppygarden_root(cfg) is None


def test_scan_roots_data_dir_only_when_no_puppygarden(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "data"))
    cfg = tmp_path / "empty.yaml"
    cfg.write_text("")
    assert plugin_scan_roots(cfg) == [tmp_path / "data" / "poll_plugins"]
    assert timer_scan_roots(cfg) == [tmp_path / "data" / "timer_plugins"]


def test_scan_roots_include_puppygarden(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "data"))
    pg = tmp_path / "repo" / "puppygarden"
    pg.mkdir(parents=True)
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "host:\n  puppygarden:\n    root: " + str(pg) + "\n")
    assert plugin_scan_roots(cfg) == [
        tmp_path / "data" / "poll_plugins",
        pg / "poll_plugins",
    ]
    assert timer_scan_roots(cfg) == [
        tmp_path / "data" / "timer_plugins",
        pg / "timer_plugins",
    ]


def test_iter_plugin_dirs_scans_both_roots(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "data"))
    pg = tmp_path / "repo" / "puppygarden"
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "host:\n  puppygarden:\n    root: " + str(pg) + "\n")

    # data-dir plugin + puppygarden plugin, distinct names → both appear.
    _make_plugin(tmp_path / "data" / "poll_plugins", "local_only")
    _make_plugin(pg / "poll_plugins", "repo_only")
    names = sorted(p.name for p in iter_plugin_dirs(config_path=cfg))
    assert names == ["local_only", "repo_only"]


def test_iter_plugin_dirs_data_dir_wins_on_collision(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "data"))
    pg = tmp_path / "repo" / "puppygarden"
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "host:\n  puppygarden:\n    root: " + str(pg) + "\n")

    local = _make_plugin(tmp_path / "data" / "poll_plugins", "shared")
    repo = _make_plugin(pg / "poll_plugins", "shared")
    (local / "marker_local").write_text("")
    (repo / "marker_repo").write_text("")

    dirs = iter_plugin_dirs(config_path=cfg)
    assert len(dirs) == 1
    assert dirs[0] == local
    assert (dirs[0] / "marker_local").exists()


def test_iter_plugin_dirs_with_collisions_reports_duplicate_names(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "data"))
    pg = tmp_path / "repo" / "puppygarden"
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "host:\n  puppygarden:\n    root: " + str(pg) + "\n")

    _make_plugin(tmp_path / "data" / "poll_plugins", "shared")
    _make_plugin(pg / "poll_plugins", "shared")
    _make_plugin(pg / "poll_plugins", "repo_only")

    dirs, duplicates = iter_plugin_dirs_with_collisions(config_path=cfg)
    assert duplicates == {"shared"}
    assert sorted(p.name for p in dirs) == ["repo_only", "shared"]
    # The data-dir copy of "shared" wins.
    shared = next(p for p in dirs if p.name == "shared")
    assert shared.parent == tmp_path / "data" / "poll_plugins"


def test_iter_plugin_dirs_with_collisions_empty_when_no_collision(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "data"))
    pg = tmp_path / "repo" / "puppygarden"
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "host:\n  puppygarden:\n    root: " + str(pg) + "\n")

    _make_plugin(tmp_path / "data" / "poll_plugins", "a")
    _make_plugin(pg / "poll_plugins", "b")
    _, duplicates = iter_plugin_dirs_with_collisions(config_path=cfg)
    assert duplicates == set()


def test_iter_timer_plugin_dirs_with_collisions_reports_duplicates(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "data"))
    pg = tmp_path / "repo" / "puppygarden"
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "host:\n  puppygarden:\n    root: " + str(pg) + "\n")

    _make_plugin(tmp_path / "data" / "timer_plugins", "dup")
    _make_plugin(pg / "timer_plugins", "dup")
    _, duplicates = iter_timer_plugin_dirs_with_collisions(config_path=cfg)
    assert duplicates == {"dup"}


def test_iter_timer_plugin_dirs_scans_both_roots(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "data"))
    pg = tmp_path / "repo" / "puppygarden"
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "host:\n  puppygarden:\n    root: " + str(pg) + "\n")

    _make_plugin(tmp_path / "data" / "timer_plugins", "t_local")
    _make_plugin(pg / "timer_plugins", "t_repo")
    names = sorted(p.name for p in iter_timer_plugin_dirs(config_path=cfg))
    assert names == ["t_local", "t_repo"]


def test_iter_plugin_dirs_with_explicit_root_scans_only_that_dir(
    tmp_path: Path, monkeypatch
) -> None:
    """Passing ``root`` scans that single dir (not the inclusive merge)."""
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "data"))
    only = tmp_path / "only"
    _make_plugin(only, "x")
    _make_plugin(tmp_path / "data" / "poll_plugins", "y")
    names = [p.name for p in iter_plugin_dirs(root=only)]
    assert names == ["x"]
