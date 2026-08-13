"""Tests for agent-authored timer plugin discovery, config, and execution."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from omnigent.host.polling.pollers.script_timer_plugins import (
    ScriptTimerPluginsPoller,
)
from omnigent.host.polling.pollers.script_timer_plugins_config import (
    ScriptTimerPluginsDefaults,
    load_script_timer_plugins_defaults,
    load_timer_plugin_config,
    load_timer_plugin_state,
    write_timer_plugin_state,
)
from omnigent.host.polling.timer_plugins_paths import (
    PLUGIN_CONFIG_NAME,
    PLUGIN_STATE_NAME,
    RUN_SCRIPT_NAME,
    iter_timer_plugin_dirs,
)


class _MockResponse:
    def raise_for_status(self) -> None:
        pass


class _MockClient:
    """Records POST calls so tests can assert emitted task events."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any]]] = []

    async def post(self, url: str, *, json: dict[str, Any]) -> _MockResponse:
        self.posts.append((url, json))
        return _MockResponse()


def _make_ctx(*, client: _MockClient | None = None) -> Any:
    class _Ctx:
        server_url = "http://127.0.0.1:8123"
        host_id = "host_test"

    _Ctx.client = client or _MockClient()  # type: ignore[attr-defined]
    return _Ctx()


def test_iter_timer_plugin_dirs_skips_hidden_and_requires_run_py(
    tmp_path: Path,
) -> None:
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / RUN_SCRIPT_NAME).write_text("print('ok')\n")
    (tmp_path / "no_entry").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / RUN_SCRIPT_NAME).write_text("print('nope')\n")

    names = [path.name for path in iter_timer_plugin_dirs(tmp_path)]
    assert names == ["good"]


def test_script_timer_plugins_defaults(tmp_path: Path) -> None:
    config = load_script_timer_plugins_defaults(tmp_path / "config.yaml")
    assert config.default_timeout_s == 120.0
    assert config.tick_s == 30.0


def test_script_timer_plugins_defaults_from_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "host:\n  polling:\n    timer_plugins:\n      default_timeout_s: 90\n      tick_s: 10\n"
    )
    config = load_script_timer_plugins_defaults(config_path)
    assert config.default_timeout_s == 90.0
    assert config.tick_s == 10.0


def test_timer_plugin_config_reads_fire_at(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "reminder"
    plugin_dir.mkdir()
    (plugin_dir / PLUGIN_CONFIG_NAME).write_text("fire_at: 1700000000\ntimeout_s: 15\n")
    defaults = ScriptTimerPluginsDefaults(default_timeout_s=120.0, tick_s=30.0)
    config = load_timer_plugin_config(plugin_dir, defaults)
    assert config.fire_at == 1700000000.0
    assert config.timeout_s == 15.0


def test_timer_plugin_config_falls_back_to_defaults(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "bare"
    plugin_dir.mkdir()
    (plugin_dir / PLUGIN_CONFIG_NAME).write_text("fire_at: 1700000000\n")
    defaults = ScriptTimerPluginsDefaults(default_timeout_s=120.0, tick_s=30.0)
    config = load_timer_plugin_config(plugin_dir, defaults)
    assert config.fire_at == 1700000000.0
    assert config.timeout_s == 120.0


def test_timer_plugin_config_missing_fire_at_returns_none(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "nofire"
    plugin_dir.mkdir()
    (plugin_dir / PLUGIN_CONFIG_NAME).write_text("timeout_s: 10\n")
    defaults = ScriptTimerPluginsDefaults(default_timeout_s=120.0, tick_s=30.0)
    config = load_timer_plugin_config(plugin_dir, defaults)
    assert config.fire_at is None
    assert config.timeout_s == 10.0


def test_load_timer_plugin_state_defaults_to_zero(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "fresh"
    plugin_dir.mkdir()
    state = load_timer_plugin_state(plugin_dir)
    assert state.fired_at == 0.0


def test_write_then_load_timer_plugin_state_roundtrip(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "roundtrip"
    plugin_dir.mkdir()
    write_timer_plugin_state(plugin_dir, fired_at=1700000123.0)
    assert (plugin_dir / PLUGIN_STATE_NAME).is_file()
    state = load_timer_plugin_state(plugin_dir)
    assert state.fired_at == 1700000123.0


def test_script_timer_plugins_poller_is_always_enabled() -> None:
    poller = ScriptTimerPluginsPoller()
    assert poller.enabled(None) is True  # type: ignore[arg-type]


async def test_run_plugin_executes_run_py(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    (plugin_dir / RUN_SCRIPT_NAME).write_text(
        "from pathlib import Path\n"
        "import os\n"
        "Path(os.environ['OMNIGENT_PLUGIN_DIR']).joinpath('ran.txt').write_text('1')\n"
    )

    ctx = _make_ctx()
    poller = ScriptTimerPluginsPoller(config_path=tmp_path / "missing.yaml")
    await poller._run_plugin(plugin_dir, ctx=ctx, fire_at=1700000000.0, timeout_s=10.0)
    assert (plugin_dir / "ran.txt").read_text() == "1"
    # Success → no fire_failed event posted.
    assert ctx.client.posts == []


async def test_poll_once_skips_plugin_not_due_yet(tmp_path: Path, monkeypatch) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    (plugin_dir / RUN_SCRIPT_NAME).write_text(
        "from pathlib import Path\n"
        "import os\n"
        "Path(os.environ['OMNIGENT_PLUGIN_DIR']).joinpath('ran.txt').write_text('1')\n"
    )
    (plugin_dir / PLUGIN_CONFIG_NAME).write_text(f"fire_at: {int(time.time()) + 3600}\n")

    import omnigent.host.polling.pollers.script_timer_plugins as timer_module

    monkeypatch.setattr(
        timer_module,
        "iter_timer_plugin_dirs",
        lambda root=None: [plugin_dir],
    )

    ctx = _make_ctx()
    poller = ScriptTimerPluginsPoller(config_path=tmp_path / "missing.yaml")
    await poller.poll_once(ctx)
    assert not (plugin_dir / "ran.txt").exists()
    assert not (plugin_dir / PLUGIN_STATE_NAME).exists()


async def test_poll_once_fires_due_plugin_and_writes_state(tmp_path: Path, monkeypatch) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    (plugin_dir / RUN_SCRIPT_NAME).write_text(
        "from pathlib import Path\n"
        "import os\n"
        "Path(os.environ['OMNIGENT_PLUGIN_DIR']).joinpath('ran.txt').write_text('1')\n"
    )
    (plugin_dir / PLUGIN_CONFIG_NAME).write_text(f"fire_at: {int(time.time()) - 60}\n")

    import omnigent.host.polling.pollers.script_timer_plugins as timer_module

    monkeypatch.setattr(
        timer_module,
        "iter_timer_plugin_dirs",
        lambda root=None: [plugin_dir],
    )

    ctx = _make_ctx()
    poller = ScriptTimerPluginsPoller(config_path=tmp_path / "missing.yaml")
    await poller.poll_once(ctx)
    assert (plugin_dir / "ran.txt").read_text() == "1"
    state = load_timer_plugin_state(plugin_dir)
    assert state.fired_at > 0


async def test_poll_once_does_not_refire_after_state_written(tmp_path: Path, monkeypatch) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    (plugin_dir / RUN_SCRIPT_NAME).write_text(
        "from pathlib import Path\n"
        "import os\n"
        "Path(os.environ['OMNIGENT_PLUGIN_DIR']).joinpath('ran.txt').write_text('1')\n"
    )
    fire_at = int(time.time()) - 60
    (plugin_dir / PLUGIN_CONFIG_NAME).write_text(f"fire_at: {fire_at}\n")
    write_timer_plugin_state(plugin_dir, fired_at=float(fire_at))

    import omnigent.host.polling.pollers.script_timer_plugins as timer_module

    monkeypatch.setattr(
        timer_module,
        "iter_timer_plugin_dirs",
        lambda root=None: [plugin_dir],
    )

    ctx = _make_ctx()
    poller = ScriptTimerPluginsPoller(config_path=tmp_path / "missing.yaml")
    await poller.poll_once(ctx)
    assert not (plugin_dir / "ran.txt").exists()


async def test_poll_once_refires_after_run_py_advances_fire_at(
    tmp_path: Path, monkeypatch
) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    fire_at = int(time.time()) - 60
    (plugin_dir / PLUGIN_CONFIG_NAME).write_text(f"fire_at: {fire_at}\n")
    # run.py re-arms fire_at to 1 hour in the future (recurring).
    (plugin_dir / RUN_SCRIPT_NAME).write_text(
        "from pathlib import Path\n"
        "import os, time, yaml\n"
        "cfg_path = Path(os.environ['OMNIGENT_PLUGIN_DIR']).joinpath('config.yaml')\n"
        "cfg = yaml.safe_load(cfg_path.read_text())\n"
        "cfg['fire_at'] = int(time.time()) + 3600\n"
        "cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False))\n"
        "Path(os.environ['OMNIGENT_PLUGIN_DIR']).joinpath('ran.txt').write_text('1')\n"
    )

    import omnigent.host.polling.pollers.script_timer_plugins as timer_module

    monkeypatch.setattr(
        timer_module,
        "iter_timer_plugin_dirs",
        lambda root=None: [plugin_dir],
    )

    ctx = _make_ctx()
    poller = ScriptTimerPluginsPoller(config_path=tmp_path / "missing.yaml")

    # Tick 1: fire_at is in the past and not yet fired → fires, run.py re-arms.
    await poller.poll_once(ctx)
    assert (plugin_dir / "ran.txt").read_text() == "1"

    # Tick 2: fire_at is now in the future → skip.
    (plugin_dir / "ran.txt").unlink()
    await poller.poll_once(ctx)
    assert not (plugin_dir / "ran.txt").exists()

    # Tick 3: advance the clock past the re-armed fire_at → fires again.
    future = time.time() + 3601
    monkeypatch.setattr(timer_module.time, "time", lambda: future)
    await poller.poll_once(ctx)
    assert (plugin_dir / "ran.txt").read_text() == "1"


async def test_poll_once_skips_plugin_with_null_fire_at(tmp_path: Path, monkeypatch) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    (plugin_dir / RUN_SCRIPT_NAME).write_text(
        "from pathlib import Path\n"
        "import os\n"
        "Path(os.environ['OMNIGENT_PLUGIN_DIR']).joinpath('ran.txt').write_text('1')\n"
    )
    (plugin_dir / PLUGIN_CONFIG_NAME).write_text("fire_at: null\n")

    import omnigent.host.polling.pollers.script_timer_plugins as timer_module

    monkeypatch.setattr(
        timer_module,
        "iter_timer_plugin_dirs",
        lambda root=None: [plugin_dir],
    )

    ctx = _make_ctx()
    poller = ScriptTimerPluginsPoller(config_path=tmp_path / "missing.yaml")
    await poller.poll_once(ctx)
    assert not (plugin_dir / "ran.txt").exists()


async def test_poll_once_marks_fired_even_when_run_py_fails(tmp_path: Path, monkeypatch) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    (plugin_dir / RUN_SCRIPT_NAME).write_text("import sys; sys.exit(1)\n")
    (plugin_dir / PLUGIN_CONFIG_NAME).write_text(f"fire_at: {int(time.time()) - 60}\n")

    import omnigent.host.polling.pollers.script_timer_plugins as timer_module

    monkeypatch.setattr(
        timer_module,
        "iter_timer_plugin_dirs",
        lambda root=None: [plugin_dir],
    )

    ctx = _make_ctx()
    poller = ScriptTimerPluginsPoller(config_path=tmp_path / "missing.yaml")
    await poller.poll_once(ctx)
    state = load_timer_plugin_state(plugin_dir)
    assert state.fired_at > 0


async def test_poll_once_posts_fire_failed_event_on_nonzero_exit(
    tmp_path: Path, monkeypatch
) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    fire_at = int(time.time()) - 60
    (plugin_dir / RUN_SCRIPT_NAME).write_text(
        "import sys; sys.stderr.write('boom'); sys.exit(2)\n"
    )
    (plugin_dir / PLUGIN_CONFIG_NAME).write_text(f"fire_at: {fire_at}\n")

    import omnigent.host.polling.pollers.script_timer_plugins as timer_module

    monkeypatch.setattr(
        timer_module,
        "iter_timer_plugin_dirs",
        lambda root=None: [plugin_dir],
    )

    ctx = _make_ctx()
    poller = ScriptTimerPluginsPoller(config_path=tmp_path / "missing.yaml")
    await poller.poll_once(ctx)

    assert len(ctx.client.posts) == 1
    url, body = ctx.client.posts[0]
    assert url == "/v1/task-events"
    assert body["event_type"] == "timer.fire_failed"
    assert body["source"] == "timer_plugin:demo"
    assert body["source_key"] == str(fire_at)
    assert body["payload"]["reason"] == "exit_nonzero"
    assert body["payload"]["exit_code"] == 2
    assert body["payload"]["plugin"] == "demo"
    assert body["payload"]["fire_at"] == fire_at
    assert "boom" in body["payload"]["detail"]
