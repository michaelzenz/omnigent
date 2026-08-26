from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest


def _load_script() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "fork_local_instance.py"
    spec = importlib.util.spec_from_file_location("fork_local_instance", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fork = _load_script()


def test_check_web_dependencies_probes_runtime_imports(tmp_path: Path, monkeypatch) -> None:
    vite = tmp_path / "web" / "node_modules" / ".bin" / "vite"
    vite.parent.mkdir(parents=True)
    vite.touch()
    calls: list[tuple[list[str], Path]] = []

    def run(command, *, cwd, capture_output, text, timeout):
        calls.append((command, cwd))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(fork, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(fork.shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(fork.subprocess, "run", run)

    assert fork._check_web_dependencies() == vite
    assert calls == [
        (
            [
                "/usr/bin/node",
                "-e",
                "Promise.all([import('@radix-ui/react-dialog'), "
                "import('@radix-ui/react-slot'), import('react-dom/client')])",
            ],
            tmp_path / "web",
        )
    ]


def test_install_web_dependencies_uses_given_registry_and_hoisting(
    tmp_path: Path, monkeypatch
) -> None:
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(fork, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(fork.shutil, "which", lambda name: "/usr/bin/pnpm")
    monkeypatch.setattr(fork.subprocess, "run", run)

    fork._install_web_dependencies("https://registry.example.test/")

    assert calls[0][0] == [
        "/usr/bin/pnpm",
        "install",
        "--frozen-lockfile",
        "--shamefully-hoist",
    ]
    assert calls[0][1]["cwd"] == tmp_path
    assert calls[0][1]["env"]["CI"] == "true"
    assert calls[0][1]["env"]["npm_config_registry"] == "https://registry.example.test/"
    assert calls[0][1]["env"]["COREPACK_NPM_REGISTRY"] == "https://registry.example.test/"


def test_prepare_web_dependencies_installs_after_failed_probe(monkeypatch) -> None:
    vite = Path("/repo/web/node_modules/.bin/vite")
    checks = iter([RuntimeError("missing Radix"), vite])
    installs = []

    def check():
        result = next(checks)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(fork, "_check_web_dependencies", check)
    monkeypatch.setattr(
        fork, "_install_web_dependencies", lambda registry=None: installs.append(registry)
    )

    assert fork._prepare_web_dependencies() == vite
    assert installs == [None]


def test_check_web_dependencies_reports_repair_command(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(fork, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(fork.shutil, "which", lambda name: None)

    with pytest.raises(RuntimeError, match="pnpm install --frozen-lockfile --shamefully-hoist"):
        fork._check_web_dependencies()


def test_open_isolated_electron_logs_and_smoke_checks(tmp_path: Path, monkeypatch) -> None:
    electron_app = tmp_path / "web" / "electron"
    electron_app.mkdir(parents=True)
    smoke_script = tmp_path / "scripts" / "check_electron_renderer.mjs"
    smoke_script.parent.mkdir()
    smoke_script.touch()
    home = tmp_path / "home"
    processes = [_FakeProcess(), _FakeProcess()]
    popen_calls = []
    run_calls = []

    def popen(command, **kwargs):
        popen_calls.append((command, kwargs))
        return processes[len(popen_calls) - 1]

    def run(command, **kwargs):
        run_calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, '{"ready":"complete"}', "")

    monkeypatch.setattr(fork, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(fork, "_find_electron_binary", lambda: "/electron")
    monkeypatch.setattr(fork, "_free_loopback_port", lambda: 43210)
    monkeypatch.setattr(fork.shutil, "which", lambda name: "/node")
    monkeypatch.setattr(fork.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(fork.sys, "platform", "darwin")
    monkeypatch.setattr(fork.subprocess, "Popen", popen)
    monkeypatch.setattr(fork.subprocess, "run", run)
    monkeypatch.setattr(fork.time, "sleep", lambda _: None)

    returned, log_path = fork._open_isolated_electron(
        "http://127.0.0.1:12345", "test-fork", tmp_path / "instance"
    )

    assert returned is processes[1]
    assert processes[0].terminated
    assert len(popen_calls) == 2
    assert log_path.parent == tmp_path / "instance" / "logs" / "electron"
    settings_path = (
        home / "Library" / "Application Support" / "Omnigent-test-fork" / "settings.json"
    )
    settings = json.loads(settings_path.read_text())
    assert settings == {"server_url": "http://127.0.0.1:12345"}
    assert "--remote-debugging-address=127.0.0.1" in popen_calls[0][0]
    assert "--remote-debugging-port=43210" in popen_calls[0][0]
    assert not any(arg.startswith("--remote-debugging") for arg in popen_calls[1][0])
    assert run_calls[0][0] == [
        "/node",
        str(smoke_script),
        "http://127.0.0.1:12345",
        "43210",
    ]


def test_close_isolated_electron_matches_exact_user_data_argument(
    tmp_path: Path, monkeypatch
) -> None:
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1 if command[0] == "pgrep" else 0)

    monkeypatch.setattr(fork.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(fork.sys, "platform", "darwin")
    monkeypatch.setattr(fork.subprocess, "run", run)

    fork._close_isolated_electron("test.one")

    assert calls[0][:3] == ["pkill", "-TERM", "-f"]
    assert "Omnigent\\-test\\.one" in calls[0][3]
    assert calls[0][3].endswith("([[:space:]]|$)")


def test_close_isolated_electron_uses_process_tree_on_windows(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(fork.sys, "platform", "win32")
    monkeypatch.setattr(
        fork.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or subprocess.CompletedProcess(command, 0),
    )

    fork._close_isolated_electron("test", 456)

    assert calls == [["taskkill", "/PID", "456", "/T", "/F"]]


class _FakeProcess:
    def __init__(self) -> None:
        self.terminated = False
        self.pid = 12345

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def poll(self) -> int | None:
        return None
