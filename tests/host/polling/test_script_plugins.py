"""Tests for agent-authored poll plugin discovery and execution."""

from __future__ import annotations

from pathlib import Path

from omnigent.host.polling.poll_plugins_paths import RUN_SCRIPT_NAME, iter_plugin_dirs
from omnigent.host.polling.pollers.script_plugins import ScriptPollPluginsPoller
from omnigent.host.polling.pollers.script_plugins_config import (
    load_script_poll_plugins_config,
)


def test_iter_plugin_dirs_skips_hidden_and_requires_run_py(tmp_path: Path) -> None:
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / RUN_SCRIPT_NAME).write_text("print('ok')\n")
    (tmp_path / "no_entry").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / RUN_SCRIPT_NAME).write_text("print('nope')\n")

    names = [path.name for path in iter_plugin_dirs(tmp_path)]
    assert names == ["good"]


def test_script_poll_plugins_config_defaults_disabled(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config = load_script_poll_plugins_config(config_path)
    assert config.enabled is False
    assert config.interval_s == 60.0


def test_script_poll_plugins_config_from_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "host:\n  polling:\n    poll_plugins:\n      enabled: true\n      interval_s: 45\n"
    )
    config = load_script_poll_plugins_config(config_path)
    assert config.enabled is True
    assert config.interval_s == 45.0


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
    await poller._run_plugin(plugin_dir, ctx=_Ctx(), timeout_s=10.0)  # type: ignore[arg-type]
    assert (plugin_dir / "ran.txt").read_text() == "1"
