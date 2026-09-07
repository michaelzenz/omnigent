"""Tests for the host daemon's SSH attach executor and remote operations."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from omnigent.host.polling.context import PollContext
from omnigent.host.ssh_attach import (
    SshAttachExecutor,
    SshAttachSettings,
    SshHostOperations,
    build_install_command,
)


@pytest.fixture(autouse=True)
def _fake_ssh_run(monkeypatch: pytest.MonkeyPatch):
    """No test may shell out to real ssh; remote commands succeed by default."""

    async def fake_ssh_run(
        _profile, command: str, *, timeout_s: float
    ) -> tuple[int, bytes, bytes]:
        del timeout_s
        if command.startswith("printf"):
            return 0, b"/home/test", b""
        return 0, b"", b""

    monkeypatch.setattr("omnigent.host.ssh_attach.ssh_run", fake_ssh_run)


def _settings(namespace: str = "server-a") -> SshAttachSettings:
    return SshAttachSettings(
        package_index_url=None,
        npm_registry_url=None,
        remote_namespace=namespace,
    )


async def _benign_local_run(args: list[str], _timeout_s: float) -> tuple[int, bytes, bytes]:
    return 0, b"", b""


def _operations(
    tmp_path: Path,
    *,
    tunnel_target: tuple[str, int] | None = ("127.0.0.1", 6767),
    command_runner=None,
) -> SshHostOperations:
    return SshHostOperations(
        remote_namespace="server-a",
        settings=_settings(),
        server_url="http://127.0.0.1:8123",
        tunnel_target=tunnel_target,
        control_dir=tmp_path / "control",
        command_runner=command_runner or _benign_local_run,
    )


def test_install_command_pins_python_and_installs_pi_in_remote_home() -> None:
    command = build_install_command("1.2.3", remote_namespace="server-a")
    assert '"$uv_bin" python install 3.12' in command
    assert '"$uv_bin" venv --python 3.12 "$target/venv"' in command
    assert 'root="$HOME/.omnigent/host/server-a"' in command
    assert "omnigent==1.2.3" in command
    assert 'pi_root="$root/harnesses/pi"' in command
    assert 'npm install --prefix "$pi_root" "$pi_spec"' in command
    assert "@earendil-works/pi-coding-agent" in command
    # No --registry flag when npm_registry_url is not set
    assert "--registry" not in command


def test_install_command_uses_custom_npm_registry() -> None:
    command = build_install_command(
        "1.2.3",
        npm_registry_url="https://npm.example.com",
        remote_namespace="server-a",
    )
    assert "--registry https://npm.example.com" in command
    assert (
        'npm install --prefix "$pi_root" --registry https://npm.example.com "$pi_spec"' in command
    )
    assert "pi_registry=https://npm.example.com" in command
    assert '"$pi_root/.registry-url"' in command


def test_registry_change_reinstalls_existing_pi(tmp_path: Path) -> None:
    root = tmp_path / ".omnigent" / "host" / "server-a"
    target = root / "versions" / "1.2.3"
    target.mkdir(parents=True)
    (target / ".complete").touch()
    pi_root = root / "harnesses" / "pi"
    pi_bin = pi_root / "node_modules" / ".bin" / "pi"
    pi_bin.parent.mkdir(parents=True)
    pi_bin.touch(mode=0o755)
    (pi_root / ".package-spec").write_text("@earendil-works/pi-coding-agent")
    (pi_root / ".registry-url").write_text("https://old.example.com")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    npm = fake_bin / "npm"
    npm.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$HOME/npm-args"\n')
    npm.chmod(0o755)
    command = build_install_command(
        "1.2.3",
        npm_registry_url="https://npm.example.com",
        remote_namespace="server-a",
    )

    subprocess.run(
        ["/bin/sh", "-c", command],
        check=True,
        env={**os.environ, "HOME": str(tmp_path), "PATH": f"{fake_bin}:{os.environ['PATH']}"},
    )

    npm_args = (tmp_path / "npm-args").read_text()
    assert "https://npm.example.com" in npm_args
    assert (pi_root / ".registry-url").read_text() == "https://npm.example.com"


async def test_matching_remote_bundle_skips_wheel_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retries must not re-upload wheels the remote already holds."""
    wheel = tmp_path / "omnigent-9.9.9-py3-none-any.whl"
    wheel.write_bytes(b"wheel-bytes")
    uploads: list[list[str]] = []

    async def fake_ssh_run(
        _profile, command: str, *, timeout_s: float
    ) -> tuple[int, bytes, bytes]:
        del timeout_s
        if command.startswith("printf"):
            return 0, b"/home/test", b""
        return 0, b"", b""

    async def fake_local_run(args: list[str], _timeout_s: float) -> tuple[int, bytes, bytes]:
        uploads.append(args)
        return 0, b"", b""

    monkeypatch.setattr("omnigent.host.ssh_attach.ssh_run", fake_ssh_run)
    operations = _operations(tmp_path, command_runner=fake_local_run)
    monkeypatch.setattr(operations, "_local_bundle", AsyncMock(return_value=[wheel]))

    await operations.ensure_installed("connection-1", "build-box", "9.9.9")

    assert not any(args[0] == "scp" for args in uploads)


async def test_remote_host_starts_with_managed_pi_on_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[str] = []

    async def fake_ssh_run(
        _profile, command: str, *, timeout_s: float
    ) -> tuple[int, bytes, bytes]:
        del timeout_s
        commands.append(command)
        return 0, b"", b""

    monkeypatch.setattr("omnigent.host.ssh_attach.ssh_run", fake_ssh_run)
    operations = _operations(tmp_path)

    await operations.start_host(
        "connection-1",
        "build-box",
        host_id="host-1",
        host_name="Build box",
        token="secret",
        socket_path="/home/test/.omnigent/server.sock",
    )

    assert len(commands) == 1
    assert 'pi_path="$root/harnesses/pi/node_modules/.bin"' in commands[0]
    assert 'env PATH="$pi_path:$PATH"' in commands[0]
    # Local-server mode: the remote daemon connects through the tunnel socket.
    assert "--server http://localhost --server-unix-socket" in commands[0]


async def test_remote_mode_starts_daemon_against_server_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a loopback server, the remote daemon connects to the server URL."""
    commands: list[str] = []

    async def fake_ssh_run(
        _profile, command: str, *, timeout_s: float
    ) -> tuple[int, bytes, bytes]:
        del timeout_s
        commands.append(command)
        return 0, b"", b""

    monkeypatch.setattr("omnigent.host.ssh_attach.ssh_run", fake_ssh_run)
    operations = SshHostOperations(
        remote_namespace="server-a",
        settings=_settings(),
        server_url="https://omnigent.example.com",
        tunnel_target=None,
        control_dir=tmp_path / "control",
    )

    await operations.start_host(
        "connection-1",
        "build-box",
        host_id="host-1",
        host_name="Build box",
        token="secret",
        socket_path=None,
    )

    assert "--server https://omnigent.example.com" in commands[0]
    assert "--server-unix-socket" not in commands[0]


async def test_tunnel_resolves_remote_home_and_verifies_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote_commands: list[str] = []
    local_commands: list[list[str]] = []

    async def fake_ssh_run(
        _profile, command: str, *, timeout_s: float
    ) -> tuple[int, bytes, bytes]:
        del timeout_s
        remote_commands.append(command)
        if command.startswith("printf"):
            return 0, b"/home/test", b""
        return 0, b"", b""

    async def fake_local_run(
        args: list[str],
        _timeout_s: float,
    ) -> tuple[int, bytes, bytes]:
        local_commands.append(args)
        if "-O" in args and "check" in args:
            return 1, b"", b""
        return 0, b"", b""

    monkeypatch.setattr("omnigent.host.ssh_attach.ssh_run", fake_ssh_run)
    operations = _operations(tmp_path, command_runner=fake_local_run)

    socket_path = await operations.ensure_tunnel("connection-1", "build-box")

    assert socket_path == "/home/test/.omnigent/server-server-a-connection-1.sock"
    start = next(args for args in local_commands if "-fN" in args)
    assert "/home/test/.omnigent/server-server-a-connection-1.sock:127.0.0.1:6767" in start
    assert any(command.startswith("rm -f /home/test/") for command in remote_commands)
    assert any(command.startswith("test -S /home/test/") for command in remote_commands)


async def test_preflight_rejects_unreachable_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote-server mode fails fast with a clear network message."""
    logs: list[tuple[str, str, str]] = []

    async def fake_ssh_run(
        _profile, command: str, *, timeout_s: float
    ) -> tuple[int, bytes, bytes]:
        del timeout_s
        if command.startswith("bash -c"):
            return 0, b"unreachable", b""
        return 0, b"", b""

    monkeypatch.setattr("omnigent.host.ssh_attach.ssh_run", fake_ssh_run)
    operations = SshHostOperations(
        remote_namespace="server-a",
        settings=_settings(),
        server_url="https://omnigent.example.com",
        tunnel_target=None,
        control_dir=tmp_path / "control",
        log=lambda _cid, phase, level, message: logs.append((phase, level, message)),
    )

    with pytest.raises(RuntimeError, match="cannot reach the server"):
        await operations.check_server_reachable("connection-1", "build-box")

    phases = [phase for phase, _level, _message in logs]
    assert "preflight" in phases


class _FakeApi:
    """Duck-typed stand-in for the host-scoped SSH API."""

    def __init__(self, *, remote_online: bool = True) -> None:
        self.remote_online_flag = remote_online
        self.phases: list[tuple[str, int, str]] = []
        self.logs: list[tuple[str, str, str]] = []
        self.registered: list[str] = []
        self.deleted: list[str] = []

    async def get(self, path: str):
        if path == "/v1/host/ssh/assignments":
            return _Response(
                {
                    "server_version": "1.0.0",
                    "settings": {},
                    "connections": [
                        {
                            "connection_id": "connection-1",
                            "label": "Build box",
                            "alias": "build-box",
                            "desired_state": "connected",
                            "phase": "queued",
                            "generation": 0,
                            "attempt": 0,
                            "host_id": "host-1",
                            "bundle_version": "1.0.0",
                        }
                    ],
                }
            )
        if path.endswith("/remote-host-status"):
            self.status_reads = getattr(self, "status_reads", 0) + 1
            online = self.remote_online_flag and self.status_reads > 1
            return _Response({"online": online})
        raise AssertionError(f"unexpected GET {path}")

    async def post(self, path: str, json: dict):
        if path.endswith("/claim"):
            return _Response({"claimed": True, "generation": 0, "remote_host_online": True})
        if path.endswith("/phase"):
            self.phases.append((json["phase"], json["generation"], json.get("last_error") or ""))
            return _Response({"accepted": True, "superseded": False})
        if path.endswith("/logs"):
            for entry in json["entries"]:
                self.logs.append((entry["phase"], entry["level"], entry["message"]))
            return _Response({"appended": len(json["entries"])})
        if path.endswith("/register-remote-host"):
            self.registered.append("host-1")
            # A freshly started remote daemon comes online shortly after; the
            # first status read still sees the old (dead) daemon as offline.
            self.remote_online_flag = True
            self.status_reads = 0
            return _Response(
                {"host_id": "host-1", "name": "Build box", "token": "t", "token_expires_at": 0}
            )
        raise AssertionError(f"unexpected POST {path}")

    async def delete(self, path: str):
        assert path.endswith("/remote-host")
        self.deleted.append(path)
        return _Response({"deleted": True})


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _ctx(api: _FakeApi, server_url: str = "http://127.0.0.1:8123") -> PollContext:
    return PollContext(server_url=server_url, host_id="daemon-1", client=api)  # type: ignore[arg-type]


def _assignment(**overrides) -> SimpleNamespace:
    row = {
        "connection_id": "connection-1",
        "label": "Build box",
        "alias": "build-box",
        "desired_state": "connected",
        "phase": "queued",
        "generation": 0,
        "attempt": 0,
        "next_attempt_at": None,
        "lease_owner": None,
        "lease_expires_at": None,
        "host_id": "host-1",
        "bundle_version": "1.0.0",
    }
    row.update(overrides)
    return SimpleNamespace(**row)


async def test_reconcile_failure_persists_backoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeApi()
    operations = _operations(tmp_path)

    async def failing_install(_connection_id: str, _alias: str, _version: str) -> None:
        raise RuntimeError("mock install failure")

    operations.ensure_installed = failing_install  # type: ignore[method-assign]
    executor = SshAttachExecutor(operations=operations)
    executor._operations = operations

    await executor._reconcile(_ctx(api), _assignment())  # type: ignore[arg-type]

    phases = [phase for phase, _gen, _err in api.phases]
    assert "installing" in phases
    assert phases[-1] == "backoff"
    errors = [entry for entry in api.logs if entry[1] == "error"]
    assert any("mock install failure" in message for _phase, _level, message in errors)


async def test_reconcile_runs_full_pipeline_to_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeApi(remote_online=False)
    operations = _operations(tmp_path)
    operations.ensure_installed = AsyncMock(return_value=None)  # type: ignore[method-assign]
    executor = SshAttachExecutor(operations=operations)
    executor._operations = operations

    await executor._reconcile(_ctx(api), _assignment())  # type: ignore[arg-type]

    phases = [phase for phase, _gen, _err in api.phases]
    assert phases == [
        "waiting_for_ssh",
        "installing",
        "opening_tunnel",
        "starting_host",
        "waiting_for_host",
        "ready",
    ]
    assert api.registered == ["host-1"]


async def test_reconcile_remote_mode_uses_preflight(
    tmp_path: Path,
) -> None:
    api = _FakeApi(remote_online=False)
    operations = SshHostOperations(
        remote_namespace="server-a",
        settings=_settings(),
        server_url="https://omnigent.example.com",
        tunnel_target=None,
        control_dir=tmp_path / "control",
    )
    operations.check_server_reachable = AsyncMock(return_value=None)  # type: ignore[method-assign]
    executor = SshAttachExecutor(operations=operations)
    executor._operations = operations

    await executor._reconcile(_ctx(api), _assignment())  # type: ignore[arg-type]

    phases = [phase for phase, _gen, _err in api.phases]
    assert "preflight" in phases
    assert "opening_tunnel" not in phases


async def test_reconcile_detaches_when_desired_state_flips(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeApi()
    operations = _operations(tmp_path)
    detach_calls: list[str] = []

    async def fake_detach(connection_id: str, alias: str) -> None:
        detach_calls.append(connection_id)

    monkeypatch.setattr(operations, "detach", fake_detach)
    executor = SshAttachExecutor(operations=operations)
    executor._operations = operations

    await executor._reconcile(
        _ctx(api),
        _assignment(desired_state="detached", phase="detaching"),  # type: ignore[arg-type]
    )

    assert detach_calls == ["connection-1"]
    assert api.deleted
    phases = [phase for phase, _gen, _err in api.phases]
    assert phases[-1] == "detached"


async def test_superseded_generation_stops_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generation bump mid-flight must end this attempt without backoff."""
    api = _FakeApi()
    released: list[str] = []

    async def post(path: str, json: dict):
        if path.endswith("/phase"):
            if json["phase"] == "installing":
                return _Response({"accepted": False, "superseded": True})
            return _Response({"accepted": True, "superseded": False})
        if path.endswith("/release-lease"):
            released.append(path)
            return _Response({"released": True})
        if path.endswith("/claim"):
            return _Response({"claimed": True, "generation": 0, "remote_host_online": True})
        raise AssertionError(f"unexpected POST {path}")

    api.post = post  # type: ignore[method-assign]
    operations = _operations(tmp_path)
    executor = SshAttachExecutor(operations=operations)
    executor._operations = operations

    await executor._reconcile(_ctx(api), _assignment())  # type: ignore[arg-type]

    assert released


def test_is_due_respects_leases_and_detached_rows() -> None:
    executor = SshAttachExecutor()
    ctx = _ctx(_FakeApi())

    connected = _assignment()
    assert executor._is_due(ctx, connected, now=100)  # type: ignore[arg-type]

    leased_elsewhere = _assignment(lease_owner="daemon-2", lease_expires_at=150)
    assert not executor._is_due(ctx, leased_elsewhere, now=100)  # type: ignore[arg-type]

    own_lease = _assignment(lease_owner="daemon-1", lease_expires_at=150)
    assert executor._is_due(ctx, own_lease, now=100)  # type: ignore[arg-type]

    expired_lease = _assignment(lease_owner="daemon-2", lease_expires_at=50)
    assert executor._is_due(ctx, expired_lease, now=100)  # type: ignore[arg-type]

    finished = _assignment(desired_state="detached", phase="detached")
    assert not executor._is_due(ctx, finished, now=100)  # type: ignore[arg-type]

    pending_cleanup = _assignment(desired_state="detached", phase="detaching")
    assert executor._is_due(ctx, pending_cleanup, now=100)  # type: ignore[arg-type]

    backed_off = _assignment(next_attempt_at=200)
    assert not executor._is_due(ctx, backed_off, now=100)  # type: ignore[arg-type]


async def test_poll_once_skips_inflight_connections(tmp_path: Path) -> None:
    """One slow reconcile must not spawn a second task for the same connection."""
    api = _FakeApi()
    started: list[str] = []

    async def post(path: str, json: dict):
        if path.endswith("/claim"):
            started.append("claim")
            await asyncio.sleep(3600)
            return _Response({"claimed": True, "generation": 0, "remote_host_online": True})
        if path.endswith("/phase"):
            return _Response({"accepted": True, "superseded": False})
        raise AssertionError(f"unexpected POST {path}")

    api.post = post  # type: ignore[method-assign]
    executor = SshAttachExecutor()

    await executor.poll_once(_ctx(api))  # type: ignore[arg-type]
    await executor.poll_once(_ctx(api))  # type: ignore[arg-type]
    await asyncio.sleep(0.05)

    assert started.count("claim") == 1
    for task in executor._inflight.values():
        task.cancel()


async def test_poll_once_skips_loopback_server_without_port() -> None:
    """A loopback URL with no port cannot be tunneled; do not pretend otherwise."""
    api = _FakeApi()
    executor = SshAttachExecutor()

    await executor.poll_once(_ctx(api, server_url="http://localhost"))  # type: ignore[arg-type]

    assert api.phases == []  # nothing claimed or reconciled


async def test_operations_log_sink_receives_keyword_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The executor's keyword-only log sink must survive the move intact."""
    events: list[tuple[str, str, str]] = []

    def sink(connection_id: str, *, phase: str, level: str, message: str) -> None:
        events.append((phase, level, message))

    async def failing_ssh_run(
        _profile, _command: str, *, timeout_s: float
    ) -> tuple[int, bytes, bytes]:
        del timeout_s
        return 1, b"", b"ssh down"

    monkeypatch.setattr("omnigent.host.ssh_attach.ssh_run", failing_ssh_run)
    operations = SshHostOperations(
        remote_namespace="server-a",
        settings=_settings(),
        server_url="http://127.0.0.1:8123",
        tunnel_target=("127.0.0.1", 6767),
        control_dir=tmp_path / "control",
        log=sink,
    )
    with pytest.raises(RuntimeError):
        await operations.check_reachable("connection-1", "build-box")

    assert events, "operations must emit log events through the sink"
    assert events[0][0] == "waiting_for_ssh"
