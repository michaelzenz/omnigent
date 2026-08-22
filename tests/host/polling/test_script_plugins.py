"""Tests for agent-authored poll plugin discovery and execution."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from omnigent.host.polling.poll_plugins_paths import (
    PLUGIN_CONFIG_NAME,
    README_NAME,
    RUN_SCRIPT_NAME,
    iter_plugin_dirs,
)
from omnigent.host.polling.pollers.script_plugins import ScriptPollPluginsPoller
from omnigent.host.polling.pollers.script_plugins_config import (
    PluginPollConfigError,
    ScriptPollPluginsDefaults,
    load_plugin_poll_config,
    load_script_poll_plugins_defaults,
    write_plugin_poll_enabled,
)


def test_iter_plugin_dirs_skips_hidden_and_requires_run_py(tmp_path: Path) -> None:
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / RUN_SCRIPT_NAME).write_text("print('ok')\n")
    (tmp_path / "no_entry").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / RUN_SCRIPT_NAME).write_text("print('nope')\n")

    names = [path.name for path in iter_plugin_dirs(tmp_path)]
    assert names == ["good"]


def test_script_poll_plugins_defaults(tmp_path: Path) -> None:
    config = load_script_poll_plugins_defaults(tmp_path / "config.yaml")
    assert config.default_interval_s == 60.0
    assert config.default_timeout_s == 120.0
    assert config.tick_s == 5.0


def test_script_poll_plugins_defaults_from_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "host:\n  polling:\n    poll_plugins:\n"
        "      default_interval_s: 45\n      default_timeout_s: 90\n      tick_s: 10\n"
    )
    config = load_script_poll_plugins_defaults(config_path)
    assert config.default_interval_s == 45.0
    assert config.default_timeout_s == 90.0
    assert config.tick_s == 10.0


def test_plugin_poll_config_uses_plugin_yaml(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "github_pr"
    plugin_dir.mkdir()
    (plugin_dir / PLUGIN_CONFIG_NAME).write_text(
        "enabled: true\ninterval_s: 30\ntimeout_s: 15\nsingleton: false\n"
    )
    defaults = ScriptPollPluginsDefaults(
        default_interval_s=60.0,
        default_timeout_s=120.0,
        tick_s=5.0,
    )
    config = load_plugin_poll_config(plugin_dir, defaults)
    assert config.interval_s == 30.0
    assert config.timeout_s == 15.0


def test_plugin_poll_config_falls_back_to_defaults(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "bare"
    plugin_dir.mkdir()
    (plugin_dir / PLUGIN_CONFIG_NAME).write_text("enabled: true\nsingleton: false\n")
    defaults = ScriptPollPluginsDefaults(
        default_interval_s=60.0,
        default_timeout_s=120.0,
        tick_s=5.0,
    )
    config = load_plugin_poll_config(plugin_dir, defaults)
    assert config.interval_s == 60.0
    assert config.timeout_s == 120.0


def test_plugin_poll_config_requires_enabled(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "bare"
    plugin_dir.mkdir()
    (plugin_dir / PLUGIN_CONFIG_NAME).write_text("singleton: false\n")
    defaults = ScriptPollPluginsDefaults(
        default_interval_s=60.0,
        default_timeout_s=120.0,
        tick_s=5.0,
    )

    with pytest.raises(PluginPollConfigError):
        load_plugin_poll_config(plugin_dir, defaults)


def test_write_plugin_poll_enabled_preserves_other_config(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    config_path = plugin_dir / PLUGIN_CONFIG_NAME
    config_path.write_text("# poll interval\nenabled: true\ninterval_s: 60\nsingleton: false\n")

    write_plugin_poll_enabled(plugin_dir, False)

    assert config_path.read_text() == (
        "# poll interval\nenabled: false\ninterval_s: 60\nsingleton: false\n"
    )


def test_plugin_poll_config_parses_singleton(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "slack_watch"
    plugin_dir.mkdir()
    (plugin_dir / PLUGIN_CONFIG_NAME).write_text(
        "enabled: true\ninterval_s: 180\nsingleton: true\nbound_role: secretary\n"
    )
    defaults = ScriptPollPluginsDefaults(
        default_interval_s=60.0,
        default_timeout_s=120.0,
        tick_s=5.0,
    )
    config = load_plugin_poll_config(plugin_dir, defaults)
    assert config.singleton is True
    assert config.bound_role == "secretary"


def test_plugin_poll_config_singleton_false_explicit(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "bare"
    plugin_dir.mkdir()
    (plugin_dir / PLUGIN_CONFIG_NAME).write_text("enabled: true\nsingleton: false\n")
    defaults = ScriptPollPluginsDefaults(
        default_interval_s=60.0,
        default_timeout_s=120.0,
        tick_s=5.0,
    )
    config = load_plugin_poll_config(plugin_dir, defaults)
    assert config.singleton is False
    assert config.bound_role is None


def test_plugin_poll_config_missing_singleton_raises(tmp_path: Path) -> None:
    from omnigent.host.polling.singleton_gate import SingletonConfigError

    plugin_dir = tmp_path / "broken"
    plugin_dir.mkdir()
    (plugin_dir / PLUGIN_CONFIG_NAME).write_text("enabled: true\ninterval_s: 30\n")
    defaults = ScriptPollPluginsDefaults(
        default_interval_s=60.0,
        default_timeout_s=120.0,
        tick_s=5.0,
    )
    with pytest.raises(SingletonConfigError):
        load_plugin_poll_config(plugin_dir, defaults)


def test_plugin_poll_config_singleton_true_without_bound_role_raises(tmp_path: Path) -> None:
    from omnigent.host.polling.singleton_gate import SingletonConfigError

    plugin_dir = tmp_path / "broken"
    plugin_dir.mkdir()
    (plugin_dir / PLUGIN_CONFIG_NAME).write_text("enabled: true\nsingleton: true\n")
    defaults = ScriptPollPluginsDefaults(
        default_interval_s=60.0,
        default_timeout_s=120.0,
        tick_s=5.0,
    )
    with pytest.raises(SingletonConfigError):
        load_plugin_poll_config(plugin_dir, defaults)


def test_script_poll_plugins_poller_is_always_enabled() -> None:
    poller = ScriptPollPluginsPoller()
    assert poller.enabled(None) is True  # type: ignore[arg-type]


async def test_run_plugin_executes_run_py(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    (plugin_dir / RUN_SCRIPT_NAME).write_text(
        "from pathlib import Path\n"
        "import os\n"
        "Path(os.environ['OMNIGENT_PLUGIN_DIR']).joinpath('ran.txt').write_text('1')\n"
    )

    class _Ctx:
        server_url = "http://127.0.0.1:8123"
        host_id = "host_test"

    poller = ScriptPollPluginsPoller(config_path=tmp_path / "missing.yaml")
    await poller._run_plugin(plugin_dir, ctx=_Ctx(), timeout_s=10.0, interval_s=60.0)  # type: ignore[arg-type]
    assert (plugin_dir / "ran.txt").read_text() == "1"


async def test_poll_once_skips_plugin_until_interval_elapsed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plugins_root = tmp_path / "poll_plugins"
    plugin_dir = plugins_root / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / RUN_SCRIPT_NAME).write_text(
        "from pathlib import Path\n"
        "import os\n"
        "Path(os.environ['OMNIGENT_PLUGIN_DIR']).joinpath('ran.txt').write_text('1')\n"
    )
    (plugin_dir / PLUGIN_CONFIG_NAME).write_text(
        "enabled: true\ninterval_s: 60\nsingleton: false\n"
    )
    (plugin_dir / README_NAME).write_text("# demo\nDocumented poll plugin.\n")

    import omnigent.host.polling.pollers.script_plugins as script_plugins_module

    monkeypatch.setattr(
        script_plugins_module,
        "iter_plugin_dirs_with_collisions",
        lambda root=None, **kwargs: ([plugin_dir], set()),
    )

    class _Ctx:
        server_url = "http://127.0.0.1:8123"
        host_id = "host_test"

    poller = ScriptPollPluginsPoller(config_path=tmp_path / "missing.yaml")
    poller._last_run["demo"] = time.monotonic()
    await poller.poll_once(_Ctx())  # type: ignore[arg-type]
    assert not (plugin_dir / "ran.txt").exists()

    poller._last_run["demo"] = time.monotonic() - 120.0
    await poller.poll_once(_Ctx())  # type: ignore[arg-type]
    assert (plugin_dir / "ran.txt").read_text() == "1"


async def test_poll_once_skips_plugin_missing_readme(tmp_path: Path, monkeypatch) -> None:
    plugins_root = tmp_path / "poll_plugins"
    plugin_dir = plugins_root / "nodoc"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / RUN_SCRIPT_NAME).write_text(
        "from pathlib import Path\n"
        "import os\n"
        "Path(os.environ['OMNIGENT_PLUGIN_DIR']).joinpath('ran.txt').write_text('1')\n"
    )
    (plugin_dir / PLUGIN_CONFIG_NAME).write_text(
        "enabled: true\ninterval_s: 1\nsingleton: false\n"
    )

    import omnigent.host.polling.pollers.script_plugins as script_plugins_module

    monkeypatch.setattr(
        script_plugins_module,
        "iter_plugin_dirs_with_collisions",
        lambda root=None, **kwargs: ([plugin_dir], set()),
    )

    class _Ctx:
        server_url = "http://127.0.0.1:8123"
        host_id = "host_test"

    poller = ScriptPollPluginsPoller(config_path=tmp_path / "missing.yaml")
    poller._last_run["nodoc"] = 0.0
    await poller.poll_once(_Ctx())  # type: ignore[arg-type]
    assert not (plugin_dir / "ran.txt").exists()


async def test_poll_once_runs_plugin_that_has_readme(tmp_path: Path, monkeypatch) -> None:
    plugins_root = tmp_path / "poll_plugins"
    plugin_dir = plugins_root / "withdoc"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / RUN_SCRIPT_NAME).write_text(
        "from pathlib import Path\n"
        "import os\n"
        "Path(os.environ['OMNIGENT_PLUGIN_DIR']).joinpath('ran.txt').write_text('1')\n"
    )
    (plugin_dir / PLUGIN_CONFIG_NAME).write_text(
        "enabled: true\ninterval_s: 1\nsingleton: false\n"
    )
    (plugin_dir / README_NAME).write_text("# withdoc\nDocumented plugin.\n")

    import omnigent.host.polling.pollers.script_plugins as script_plugins_module

    monkeypatch.setattr(
        script_plugins_module,
        "iter_plugin_dirs_with_collisions",
        lambda root=None, **kwargs: ([plugin_dir], set()),
    )

    class _Ctx:
        server_url = "http://127.0.0.1:8123"
        host_id = "host_test"

    poller = ScriptPollPluginsPoller(config_path=tmp_path / "missing.yaml")
    poller._last_run["withdoc"] = 0.0
    await poller.poll_once(_Ctx())  # type: ignore[arg-type]
    assert (plugin_dir / "ran.txt").read_text() == "1"


async def test_poll_once_does_not_run_disabled_plugin(tmp_path: Path, monkeypatch) -> None:
    plugin_dir = tmp_path / "poll_plugins" / "disabled"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / RUN_SCRIPT_NAME).write_text(
        "from pathlib import Path\n"
        "import os\n"
        "Path(os.environ['OMNIGENT_PLUGIN_DIR']).joinpath('ran.txt').write_text('1')\n"
    )
    (plugin_dir / PLUGIN_CONFIG_NAME).write_text(
        "enabled: false\ninterval_s: 1\nsingleton: false\n"
    )
    (plugin_dir / README_NAME).write_text("# disabled\nDocumented plugin.\n")

    import omnigent.host.polling.pollers.script_plugins as script_plugins_module

    monkeypatch.setattr(
        script_plugins_module,
        "iter_plugin_dirs_with_collisions",
        lambda root=None, **kwargs: ([plugin_dir], set()),
    )

    class _Ctx:
        server_url = "http://127.0.0.1:8123"
        host_id = "host_test"

    poller = ScriptPollPluginsPoller(config_path=tmp_path / "missing.yaml")
    await poller.poll_once(_Ctx())  # type: ignore[arg-type]

    assert not (plugin_dir / "ran.txt").exists()
    [health] = poller._health.snapshot()
    assert health.enabled is False
    assert health.outcome == "disabled"
