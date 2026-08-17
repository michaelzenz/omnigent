"""Tests for durable SSH host installation reconciliation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from omnigent.db.db_models import OmnigentBase
from omnigent.db.utils import get_or_create_engine
from omnigent.server.ssh_host_manager import (
    SshHostInstallationManager,
    SshHostOperations,
    build_install_command,
)
from omnigent.ssh_connections_store import SshConnectionProfile
from omnigent.stores.ssh_host_installation_store import SshHostInstallationStore
from omnigent.version import VERSION


@pytest.fixture
def lifecycle_store(tmp_path: Path) -> SshHostInstallationStore:
    uri = f"sqlite:///{tmp_path / 'ssh-manager.db'}"
    OmnigentBase.metadata.create_all(get_or_create_engine(uri))
    return SshHostInstallationStore(uri)


def _profile() -> SshConnectionProfile:
    return SshConnectionProfile(
        id="connection-1",
        label="Build box",
        alias="build-box",
        created_at="2026-08-06T00:00:00+00:00",
    )


def test_lease_is_cas_and_expired_lease_is_reclaimed(
    lifecycle_store: SshHostInstallationStore,
) -> None:
    lifecycle_store.sync_connections(
        {"connection-1": _profile()},
        bundle_version=VERSION,
        owner="local",
    )
    first = lifecycle_store.acquire(
        "connection-1",
        lease_owner="worker-a",
        lease_seconds=30,
        now=100,
    )
    assert first is not None
    assert (
        lifecycle_store.acquire(
            "connection-1",
            lease_owner="worker-b",
            lease_seconds=30,
            now=110,
        )
        is None
    )
    reclaimed = lifecycle_store.acquire(
        "connection-1",
        lease_owner="worker-b",
        lease_seconds=30,
        now=131,
    )
    assert reclaimed is not None
    assert reclaimed.lease_owner == "worker-b"


def test_removed_profile_keeps_alias_for_crash_safe_cleanup(
    lifecycle_store: SshHostInstallationStore,
) -> None:
    profile = _profile()
    lifecycle_store.sync_connections(
        {profile.id: profile},
        bundle_version=VERSION,
        owner="local",
    )
    lifecycle_store.sync_connections({}, bundle_version=VERSION, owner="local")

    row = lifecycle_store.snapshots().get(profile.id)
    assert row is not None
    assert row.desired_state == "detached"
    assert row.phase == "detaching"
    assert row.ssh_alias == profile.alias


def test_removal_supersedes_leased_reconciliation(
    lifecycle_store: SshHostInstallationStore,
) -> None:
    profile = _profile()
    lifecycle_store.sync_connections(
        {profile.id: profile},
        bundle_version=VERSION,
        owner="local",
    )
    leased = lifecycle_store.acquire(
        profile.id,
        lease_owner="worker-a",
        lease_seconds=30,
    )
    assert leased is not None

    lifecycle_store.sync_connections({}, bundle_version=VERSION, owner="local")

    assert not lifecycle_store.set_phase(
        profile.id,
        lease_owner="worker-a",
        generation=leased.generation,
        phase="ready",
        release=True,
    )
    current = lifecycle_store.snapshots().get(profile.id)
    assert current is not None
    assert current.phase == "detaching"
    assert current.generation == leased.generation + 1


def test_retry_does_not_revoke_active_lease(
    lifecycle_store: SshHostInstallationStore,
) -> None:
    profile = _profile()
    lifecycle_store.sync_connections(
        {profile.id: profile},
        bundle_version=VERSION,
        owner="local",
    )
    leased = lifecycle_store.acquire(
        profile.id,
        lease_owner="worker-a",
        lease_seconds=30,
    )
    assert leased is not None

    assert lifecycle_store.retry_now(profile.id)
    current = lifecycle_store.snapshots().get(profile.id)
    assert current is not None
    assert current.lease_owner == "worker-a"
    assert current.generation == leased.generation + 1


class _FakeHostStore:
    def __init__(self) -> None:
        self.online = False
        self.registered: list[str] = []
        self.deleted: list[str] = []

    def is_online(self, _host_id: str) -> bool:
        return self.online

    def register_ssh_host(self, *, host_id: str, **_kwargs: object) -> None:
        self.registered.append(host_id)

    def delete_host(self, host_id: str) -> None:
        self.deleted.append(host_id)


class _FakeOperations:
    def __init__(self, host_store: _FakeHostStore, *, fail_install: bool = False) -> None:
        self.host_store = host_store
        self.fail_install = fail_install
        self.calls: list[str] = []

    async def check_reachable(self, _profile: SshConnectionProfile) -> None:
        self.calls.append("reachable")

    async def ensure_installed(self, _profile: SshConnectionProfile, _version: str) -> None:
        self.calls.append("install")
        if self.fail_install:
            raise RuntimeError("mock install failure")

    async def ensure_tunnel(self, _profile: SshConnectionProfile) -> str:
        self.calls.append("tunnel")
        return "/home/test/.omnigent/server-connection-1.sock"

    async def start_host(self, _profile: SshConnectionProfile, **_kwargs: str) -> None:
        self.calls.append("start")
        self.host_store.online = True

    async def detach(self, _connection_id: str, _profile: SshConnectionProfile) -> None:
        self.calls.append("detach")


async def test_stage_failure_persists_and_retry_is_idempotent(
    lifecycle_store: SshHostInstallationStore,
) -> None:
    profile = _profile()
    lifecycle_store.sync_connections({profile.id: profile}, bundle_version=VERSION, owner="local")
    host_store = _FakeHostStore()
    operations = _FakeOperations(host_store, fail_install=True)
    manager = SshHostInstallationManager(
        store=lifecycle_store,
        host_store=host_store,  # type: ignore[arg-type]
        local_host="127.0.0.1",
        local_port=8123,
        operations=operations,  # type: ignore[arg-type]
    )
    row = lifecycle_store.acquire(
        profile.id,
        lease_owner=manager.worker_id,
        lease_seconds=180,
    )
    assert row is not None
    await manager._reconcile(row, profile)
    failed = lifecycle_store.snapshots().get(profile.id)
    assert failed is not None
    assert failed.phase == "backoff"
    assert failed.attempt == 1
    assert failed.last_error == "mock install failure"

    assert manager.retry(profile.id)
    operations.fail_install = False
    row = lifecycle_store.acquire(
        profile.id,
        lease_owner=manager.worker_id,
        lease_seconds=180,
    )
    assert row is not None
    await manager._reconcile(row, profile)
    ready = lifecycle_store.snapshots().get(profile.id)
    assert ready is not None
    assert ready.phase == "ready"
    assert host_store.registered == [row.host_id]

    operations.calls.clear()
    row = lifecycle_store.acquire(
        profile.id,
        lease_owner=manager.worker_id,
        lease_seconds=180,
        now=(ready.next_attempt_at or 0) + 1,
    )
    assert row is not None
    await manager._reconcile(row, profile)
    assert operations.calls == []


async def test_startup_resumes_queued_installation(
    lifecycle_store: SshHostInstallationStore,
) -> None:
    profile = _profile()
    lifecycle_store.sync_connections({profile.id: profile}, bundle_version=VERSION, owner="local")
    host_store = _FakeHostStore()
    operations = _FakeOperations(host_store)
    manager = SshHostInstallationManager(
        store=lifecycle_store,
        host_store=host_store,  # type: ignore[arg-type]
        local_host="127.0.0.1",
        local_port=8123,
        operations=operations,  # type: ignore[arg-type]
        profile_reader=lambda: [profile],
        scan_interval_s=0.01,
    )
    await manager.start()
    try:
        async with asyncio.timeout(2):
            while lifecycle_store.snapshots().get(profile.id).phase != "ready":  # type: ignore[union-attr]
                await asyncio.sleep(0.01)
    finally:
        await manager.stop()
    assert operations.calls == ["reachable", "install", "tunnel", "start"]


async def test_slow_connection_does_not_block_its_peers(
    lifecycle_store: SshHostInstallationStore,
) -> None:
    """A host stuck installing must not stall reconciliation for the others."""
    slow = SshConnectionProfile(
        id="slow-box",
        label="Slow box",
        alias="slow-box",
        created_at="2026-08-06T00:00:00+00:00",
    )
    fast = _profile()
    lifecycle_store.sync_connections(
        {slow.id: slow, fast.id: fast},
        bundle_version=VERSION,
        owner="local",
    )
    host_store = _FakeHostStore()
    operations = _FakeOperations(host_store)
    stall = asyncio.Event()
    original_install = operations.ensure_installed

    async def blocking_install(profile: SshConnectionProfile, version: str) -> None:
        if profile.id == slow.id:
            await stall.wait()
        await original_install(profile, version)

    operations.ensure_installed = blocking_install  # type: ignore[method-assign]
    manager = SshHostInstallationManager(
        store=lifecycle_store,
        host_store=host_store,  # type: ignore[arg-type]
        local_host="127.0.0.1",
        local_port=8123,
        operations=operations,  # type: ignore[arg-type]
        profile_reader=lambda: [slow, fast],
        scan_interval_s=0.01,
    )
    await manager.start()
    try:
        async with asyncio.timeout(2):
            while lifecycle_store.snapshots()[fast.id].phase != "ready":
                await asyncio.sleep(0.01)
        assert lifecycle_store.snapshots()[slow.id].phase == "installing"
    finally:
        stall.set()
        await manager.stop()


async def test_matching_remote_bundle_skips_wheel_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retries must not re-upload wheels the remote already holds."""
    profile = _profile()
    wheel = tmp_path / "omnigent-9.9.9-py3-none-any.whl"
    wheel.write_bytes(b"wheel-bytes")
    uploads: list[list[str]] = []

    async def fake_ssh_run(
        _profile: SshConnectionProfile,
        command: str,
        *,
        timeout_s: float,
    ) -> tuple[int, bytes, bytes]:
        del timeout_s
        if command.startswith("printf"):
            return 0, b"/home/test", b""
        return 0, b"", b""

    async def fake_local_run(args: list[str], _timeout_s: float) -> tuple[int, bytes, bytes]:
        uploads.append(args)
        return 0, b"", b""

    monkeypatch.setattr("omnigent.server.ssh_host_manager.ssh_run", fake_ssh_run)
    operations = SshHostOperations(
        local_host="127.0.0.1",
        local_port=6767,
        command_runner=fake_local_run,
        control_dir=tmp_path / "control",
    )
    monkeypatch.setattr(operations, "_local_bundle", AsyncMock(return_value=[wheel]))

    await operations.ensure_installed(profile, "9.9.9")

    assert not any(args[0] == "scp" for args in uploads)


def test_install_command_pins_python_and_uses_versioned_home_path() -> None:
    command = build_install_command("1.2.3")
    assert '"$uv_bin" python install 3.12' in command
    assert '"$uv_bin" venv --python 3.12 "$target/venv"' in command
    assert 'root="$HOME/.omnigent/host"' in command
    assert "omnigent==1.2.3" in command


async def test_tunnel_resolves_remote_home_and_verifies_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile()
    remote_commands: list[str] = []
    local_commands: list[list[str]] = []

    async def fake_ssh_run(
        _profile: SshConnectionProfile,
        command: str,
        *,
        timeout_s: float,
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

    monkeypatch.setattr("omnigent.server.ssh_host_manager.ssh_run", fake_ssh_run)
    operations = SshHostOperations(
        local_host="127.0.0.1",
        local_port=6767,
        command_runner=fake_local_run,
        control_dir=tmp_path,
    )

    socket_path = await operations.ensure_tunnel(profile)

    assert socket_path == "/home/test/.omnigent/server-connection-1.sock"
    start = next(args for args in local_commands if "-fN" in args)
    assert "/home/test/.omnigent/server-connection-1.sock:127.0.0.1:6767" in start
    assert any(command.startswith("rm -f /home/test/") for command in remote_commands)
    assert any(command.startswith("test -S /home/test/") for command in remote_commands)


async def test_tunnel_restarts_existing_control_master(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile()
    local_commands: list[list[str]] = []

    async def fake_ssh_run(
        _profile: SshConnectionProfile,
        command: str,
        *,
        timeout_s: float,
    ) -> tuple[int, bytes, bytes]:
        del timeout_s
        if command.startswith("printf"):
            return 0, b"/home/test", b""
        return 0, b"", b""

    async def fake_local_run(
        args: list[str],
        _timeout_s: float,
    ) -> tuple[int, bytes, bytes]:
        local_commands.append(args)
        return 0, b"", b""

    monkeypatch.setattr("omnigent.server.ssh_host_manager.ssh_run", fake_ssh_run)
    operations = SshHostOperations(
        local_host="127.0.0.1",
        local_port=6767,
        command_runner=fake_local_run,
        control_dir=tmp_path,
    )

    await operations.ensure_tunnel(profile)

    assert any("-O" in args and "exit" in args for args in local_commands)
    assert any("-fN" in args for args in local_commands)
