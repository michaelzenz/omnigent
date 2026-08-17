"""Durable reconciler for hosts attached through configured SSH profiles."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import shlex
import shutil
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from functools import partial
from pathlib import Path

from omnigent.db.utils import now_epoch
from omnigent.ssh_connections_store import (
    SshConnectionProfile,
    read_ssh_connections,
    read_ssh_settings,
)
from omnigent.ssh_remote import ssh_run
from omnigent.stores.host_store import HostStore
from omnigent.stores.ssh_host_installation_store import (
    SshHostInstallation,
    SshHostInstallationStore,
)
from omnigent.version import VERSION

_logger = logging.getLogger(__name__)
_LEASE_SECONDS = 30
_LEASE_RENEW_SECONDS = 10
_READY_RECHECK_SECONDS = 15
_HOST_READY_TIMEOUT_SECONDS = 90
_TOKEN_TTL_SECONDS = 366 * 24 * 60 * 60
_MAX_BACKOFF_SECONDS = 15 * 60

CommandRunner = Callable[[list[str], float], Awaitable[tuple[int, bytes, bytes]]]
InstallCommandBuilder = Callable[
    [str, str | None, str | None, str | None, str | None],
    str,
]


class _ReconciliationSuperseded(Exception):
    """Durable intent changed while a worker handled an older generation."""


def _main_wheel(bundles: list[Path]) -> Path:
    """Pick the application wheel out of a built bundle.

    Sibling SDK distributions normalize their dashes to underscores in wheel
    filenames (``omnigent_client-``), so only the app matches ``omnigent-``.
    """
    for bundle in bundles:
        if bundle.name.startswith("omnigent-"):
            return bundle
    names = ", ".join(bundle.name for bundle in bundles)
    raise RuntimeError(f"no omnigent application wheel among built bundles: {names}")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def _run_local_command(args: list[str], timeout_s: float) -> tuple[int, bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout_s)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise
    return process.returncode or 0, stdout, stderr


def build_install_command(
    version: str,
    package_spec: str | None = None,
    bundle_sha256: str | None = None,
    index_url: str | None = None,
    find_links: str | None = None,
) -> str:
    """Build the idempotent remote installation command."""
    quoted_version = shlex.quote(version)
    quoted_package_spec = shlex.quote(package_spec or f"omnigent=={version}")
    index_env = f"UV_INDEX_URL={shlex.quote(index_url)} " if index_url else ""
    find_links_arg = f"--find-links {shlex.quote(find_links)} " if find_links else ""
    checksum_guard = '[ ! -f "$target/.complete" ]'
    checksum_write = ""
    if bundle_sha256 is not None:
        quoted_checksum = shlex.quote(bundle_sha256)
        checksum_guard += (
            f' || [ "$(cat "$target/.bundle-sha256" 2>/dev/null || true)" != {quoted_checksum} ]'
        )
        checksum_write = f'printf %s {quoted_checksum} > "$target/.bundle-sha256"; '
    return (
        "set -eu; "
        'root="$HOME/.omnigent/host"; '
        f"version={quoted_version}; "
        'target="$root/versions/$version"; '
        'mkdir -p "$root/versions"; '
        f"if {checksum_guard}; then "
        'rm -rf "$root/versions/$version"; '
        'mkdir -p "$target"; '
        'if command -v uv >/dev/null 2>&1; then uv_bin="$(command -v uv)"; '
        "else curl -LsSf https://astral.sh/uv/install.sh | sh; "
        'uv_bin="$HOME/.local/bin/uv"; fi; '
        '"$uv_bin" python install 3.12; '
        '"$uv_bin" venv --python 3.12 "$target/venv"; '
        f'{index_env}"$uv_bin" pip install --python "$target/venv/bin/python" '
        f"{find_links_arg}{quoted_package_spec}; "
        f"{checksum_write}"
        'touch "$target/.complete"; '
        "fi; "
        'if [ -e "$root/current" ] && [ ! -L "$root/current" ]; then '
        'mv "$root/current" "$root/current.legacy.$(date +%s).$$"; fi; '
        'link="$root/.current-$$"; rm -f "$link"; ln -s "$target" "$link"; '
        'rm -f "$root/current"; mv "$link" "$root/current"'
    )


class SshHostOperations:
    """Reality-checking SSH operations, injectable as a unit in tests."""

    def __init__(
        self,
        *,
        local_host: str,
        local_port: int,
        command_runner: CommandRunner = _run_local_command,
        install_command_builder: InstallCommandBuilder = build_install_command,
        control_dir: Path | None = None,
        python_index_url: str | None = None,
    ) -> None:
        self._local_host = local_host
        self._local_port = local_port
        self._run = command_runner
        self._install_command_builder = install_command_builder
        self._python_index_url_override = python_index_url
        self._control_dir = control_dir or Path.home() / ".omnigent" / "host" / "ssh-control"
        self._bundle_dir = self._control_dir.parent / "bundles"
        self._local_bundles: dict[str, list[Path] | None] = {}

    def _python_index_url(self) -> str | None:
        if self._python_index_url_override is not None:
            return self._python_index_url_override
        return read_ssh_settings().package_index_url

    async def remote_home(self, profile: SshConnectionProfile) -> str:
        """Resolve the remote account's absolute home directory."""
        code, stdout, stderr = await ssh_run(
            profile,
            'printf "%s" "$HOME"',
            timeout_s=15,
        )
        if code != 0:
            raise RuntimeError(
                (stderr or stdout).decode().strip() or "could not resolve remote home"
            )
        home = stdout.decode().strip()
        if not home.startswith("/"):
            raise RuntimeError("remote HOME is not an absolute path")
        return home

    async def remote_socket(self, profile: SshConnectionProfile) -> str:
        """Build the absolute remote socket path for the reverse forward.

        OpenSSH doesn't shell-expand ``~`` in stream-local ``-R`` paths, so the
        remote home has to be resolved before constructing the forward.
        """
        return f"{await self.remote_home(profile)}/.omnigent/server-{profile.id}.sock"

    def _control_path(self, connection_id: str, alias: str) -> Path:
        identity = f"{connection_id}\0{alias}\0{self._local_host}\0{self._local_port}"
        digest = hashlib.sha256(identity.encode()).hexdigest()[:32]
        return self._control_dir / f"{digest}.sock"

    async def check_reachable(self, profile: SshConnectionProfile) -> None:
        code, stdout, stderr = await ssh_run(profile, "true", timeout_s=15)
        if code != 0:
            raise RuntimeError((stderr or stdout).decode().strip() or "SSH is unreachable")

    async def ensure_installed(self, profile: SshConnectionProfile, version: str) -> None:
        package_spec: str | None = None
        bundle_sha256: str | None = None
        find_links: str | None = None
        local_bundles = await self._local_bundle(version)
        if local_bundles is not None:
            main_bundle = _main_wheel(local_bundles)
            remote_package_dir = f"{await self.remote_home(profile)}/.omnigent/host/packages"
            bundle_hashes = [
                await asyncio.to_thread(_file_sha256, bundle) for bundle in local_bundles
            ]
            bundle_sha256 = hashlib.sha256("".join(bundle_hashes).encode()).hexdigest()
            package_spec = f"{remote_package_dir}/{main_bundle.name}"
            find_links = remote_package_dir
            if not await self._remote_bundle_matches(profile, version, bundle_sha256):
                await self._upload_bundles(profile, local_bundles, remote_package_dir)
        command = self._install_command_builder(
            version,
            package_spec,
            bundle_sha256,
            self._python_index_url(),
            find_links,
        )
        code, stdout, stderr = await ssh_run(profile, command, timeout_s=600)
        if code != 0:
            raise RuntimeError((stderr or stdout).decode().strip() or "remote install failed")

    async def _remote_bundle_matches(
        self,
        profile: SshConnectionProfile,
        version: str,
        bundle_sha256: str,
    ) -> bool:
        """Report whether the remote already holds this exact wheel bundle.

        Retries re-run installation often, so skip re-uploading megabytes of
        wheels the remote already has.
        """
        command = (
            'root="$HOME/.omnigent/host"; '
            f"version={shlex.quote(version)}; "
            'target="$root/versions/$version"; '
            '[ -f "$target/.complete" ] && '
            '[ "$(cat "$target/.bundle-sha256" 2>/dev/null || true)" = '
            f"{shlex.quote(bundle_sha256)} ]"
        )
        code, _, _ = await ssh_run(profile, command, timeout_s=15)
        return code == 0

    async def _upload_bundles(
        self,
        profile: SshConnectionProfile,
        local_bundles: list[Path],
        remote_package_dir: str,
    ) -> None:
        code, stdout, stderr = await ssh_run(
            profile,
            f"mkdir -p {shlex.quote(remote_package_dir)}",
            timeout_s=30,
        )
        if code != 0:
            raise RuntimeError(
                (stderr or stdout).decode().strip() or "remote package directory failed"
            )
        for local_bundle in local_bundles:
            remote_bundle = f"{remote_package_dir}/{local_bundle.name}"
            code, stdout, stderr = await self._run(
                [
                    "scp",
                    "-q",
                    str(local_bundle),
                    f"{profile.alias}:{shlex.quote(remote_bundle)}",
                ],
                300,
            )
            if code != 0:
                raise RuntimeError((stderr or stdout).decode().strip() or "wheel upload failed")

    async def _local_bundle(self, version: str) -> list[Path] | None:
        """Build and cache the application and sibling SDK wheels."""
        if version in self._local_bundles:
            return self._local_bundles[version]
        source_root = Path(__file__).resolve().parents[2]
        if not (source_root / "pyproject.toml").is_file():
            self._local_bundles[version] = None
            return None
        uv = shutil.which("uv")
        if uv is None:
            self._local_bundles[version] = None
            return None
        output_dir = self._bundle_dir / version
        output_dir.mkdir(parents=True, exist_ok=True)
        for stale in output_dir.glob("*.whl"):
            stale.unlink()
        projects = [
            source_root / "sdks" / "python-client",
            source_root / "sdks" / "ui",
            source_root,
        ]
        build_prefix = [uv]
        index_url = self._python_index_url()
        if index_url:
            build_prefix = ["env", f"UV_INDEX_URL={index_url}", uv]
        for project in projects:
            code, stdout, stderr = await self._run(
                [
                    *build_prefix,
                    "build",
                    "--wheel",
                    "--no-build-isolation",
                    "--out-dir",
                    str(output_dir),
                    str(project),
                ],
                300,
            )
            if code != 0:
                code, stdout, stderr = await self._run(
                    [
                        *build_prefix,
                        "build",
                        "--wheel",
                        "--out-dir",
                        str(output_dir),
                        str(project),
                    ],
                    300,
                )
            if code != 0:
                raise RuntimeError(
                    (stderr or stdout).decode().strip() or "local wheel build failed"
                )
        built = sorted(output_dir.glob("*.whl"))
        if len(built) < len(projects):
            raise RuntimeError("local bundle build did not produce all Omnigent wheels")
        self._local_bundles[version] = built
        return built

    async def ensure_tunnel(self, profile: SshConnectionProfile) -> str:
        self._control_dir.mkdir(parents=True, exist_ok=True)
        control_path = self._control_path(profile.id, profile.alias)
        check = ["ssh", "-S", str(control_path), "-O", "check", profile.alias]
        code, _, _ = await self._run(check, 10)
        if code == 0:
            await self._run(["ssh", "-S", str(control_path), "-O", "exit", profile.alias], 10)
        with suppress(FileNotFoundError):
            control_path.unlink()
        remote_socket = await self.remote_socket(profile)
        code, stdout, stderr = await ssh_run(
            profile,
            f"rm -f {shlex.quote(remote_socket)}",
            timeout_s=15,
        )
        if code != 0:
            raise RuntimeError(
                (stderr or stdout).decode().strip() or "failed to remove stale remote socket"
            )
        reverse = f"{remote_socket}:{self._local_host}:{self._local_port}"
        start = [
            "ssh",
            "-M",
            "-S",
            str(control_path),
            "-o",
            "ControlPersist=yes",
            "-o",
            # Arca profiles may carry unrelated RemoteForward entries. One
            # colliding must not abort this connection; verify our socket
            # explicitly below instead.
            "ExitOnForwardFailure=no",
            "-o",
            "StreamLocalBindUnlink=yes",
            "-o",
            "LogLevel=ERROR",
            "-fN",
            "-R",
            reverse,
            profile.alias,
        ]
        code, stdout, stderr = await self._run(start, 30)
        if code != 0:
            raise RuntimeError((stderr or stdout).decode().strip() or "reverse tunnel failed")
        code, stdout, stderr = await ssh_run(
            profile,
            f"test -S {shlex.quote(remote_socket)}",
            timeout_s=15,
        )
        if code != 0:
            raise RuntimeError(
                (stderr or stdout).decode().strip()
                or "reverse tunnel did not create the remote Unix socket"
            )
        return remote_socket

    async def start_host(
        self,
        profile: SshConnectionProfile,
        *,
        host_id: str,
        host_name: str,
        token: str,
        socket_path: str,
    ) -> None:
        values = {
            "OMNIGENT_HOST_TOKEN": token,
            "OMNIGENT_HOST_ID": host_id,
            "OMNIGENT_HOST_NAME": host_name,
        }
        env = " ".join(f"{key}={shlex.quote(value)}" for key, value in values.items())
        runtime_name = shlex.quote(profile.id)
        command = (
            'set -eu; root="$HOME/.omnigent/host"; '
            f'runtime="$root/runtimes"/{runtime_name}; mkdir -p "$runtime"; '
            'if [ -f "$runtime/host.pid" ]; then '
            'pid="$(cat "$runtime/host.pid" 2>/dev/null || true)"; '
            'if [ -n "$pid" ] && ps -p "$pid" -o command= 2>/dev/null '
            '| grep -F "$root/current/venv/bin/omnigent host" >/dev/null; '
            'then kill "$pid" 2>/dev/null || true; fi; fi; '
            f'nohup env {env} "$root/current/venv/bin/omnigent" host '
            f"--server http://localhost --server-unix-socket {shlex.quote(socket_path)} "
            '--non-interactive >"$runtime/host.log" 2>&1 < /dev/null & '
            'echo "$!" >"$runtime/host.pid"'
        )
        code, stdout, stderr = await ssh_run(profile, command, timeout_s=30)
        if code != 0:
            raise RuntimeError((stderr or stdout).decode().strip() or "remote host start failed")

    async def detach(self, connection_id: str, profile: SshConnectionProfile) -> None:
        control_path = self._control_path(connection_id, profile.alias)
        await self._run(["ssh", "-S", str(control_path), "-O", "exit", profile.alias], 10)
        with suppress(FileNotFoundError):
            control_path.unlink()
        runtime_name = shlex.quote(connection_id)
        command = (
            'root="$HOME/.omnigent/host"; '
            f'runtime="$root/runtimes"/{runtime_name}; '
            'if [ -f "$runtime/host.pid" ]; then '
            'pid="$(cat "$runtime/host.pid" 2>/dev/null || true)"; '
            'if [ -n "$pid" ] && ps -p "$pid" -o command= 2>/dev/null '
            '| grep -F "$root/current/venv/bin/omnigent host" >/dev/null; '
            'then kill "$pid" 2>/dev/null || true; fi; '
            'rm -f "$runtime/host.pid"; fi; '
            f'rm -f "$HOME/.omnigent/server-{connection_id}.sock"'
        )
        with suppress(Exception):
            await ssh_run(profile, command, timeout_s=15)


class SshHostInstallationManager:
    """Continuously converges config profiles from durable DB state."""

    def __init__(
        self,
        *,
        store: SshHostInstallationStore,
        host_store: HostStore,
        local_host: str,
        local_port: int,
        operations: SshHostOperations | None = None,
        default_owner: str = "local",
        scan_interval_s: float = 2.0,
        profile_reader: Callable[[], list[SshConnectionProfile]] = read_ssh_connections,
    ) -> None:
        self.store = store
        self.host_store = host_store
        self.operations = operations or SshHostOperations(
            local_host=local_host,
            local_port=local_port,
        )
        self.default_owner = default_owner
        self.scan_interval_s = scan_interval_s
        self.profile_reader = profile_reader
        self.worker_id = uuid.uuid4().hex
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._profiles: dict[str, SshConnectionProfile] = {}
        self._inflight: dict[str, asyncio.Task[None]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._loop = asyncio.get_running_loop()
        self.sync_profiles(self.profile_reader())
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="ssh-host-installation-manager")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        inflight = list(self._inflight.values())
        for task in inflight:
            task.cancel()
        for task in inflight:
            with suppress(asyncio.CancelledError):
                await task
        self._inflight.clear()

    def wake(self) -> None:
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._wake.set)
        else:
            self._wake.set()

    def sync_profiles(
        self,
        profiles: list[SshConnectionProfile],
        *,
        owner: str | None = None,
    ) -> None:
        self._profiles = {profile.id: profile for profile in profiles}
        existing = self.store.snapshots()
        for profile in profiles:
            row = existing.get(profile.id)
            if profile.owner is not None and row is not None and row.owner != profile.owner:
                self.host_store.reassign_ssh_host_owner(row.host_id, profile.owner)
        self.store.sync_connections(
            self._profiles,
            bundle_version=VERSION,
            owner=owner or self.default_owner,
        )
        self.wake()

    def retry(self, connection_id: str) -> bool:
        changed = self.store.retry_now(connection_id)
        if changed:
            self.wake()
        return changed

    def requeue_connected_installations(self) -> None:
        """Re-run installation when remote package index settings change."""
        changed = False
        for row in self.store.snapshots().values():
            if row.desired_state != "connected":
                continue
            if self.store.retry_now(row.connection_id):
                changed = True
        if changed:
            self.wake()

    def snapshot(self) -> dict[str, SshHostInstallation]:
        return self.store.snapshots()

    async def _run(self) -> None:
        while not self._stopping:
            try:
                await self._scan_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("SSH host reconciliation scan failed")
            self._wake.clear()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self.scan_interval_s)

    async def _scan_once(self) -> None:
        """Lease every due connection and reconcile them concurrently.

        A remote install can run for minutes, so one slow or unreachable host
        must not stall reconciliation for the others.
        """
        profiles = dict(self._profiles)
        for candidate in await asyncio.to_thread(self.store.list_candidates):
            connection_id = candidate.connection_id
            if connection_id in self._inflight:
                continue
            leased = await asyncio.to_thread(
                self.store.acquire,
                connection_id,
                lease_owner=self.worker_id,
                lease_seconds=_LEASE_SECONDS,
            )
            if leased is None:
                continue
            task = asyncio.create_task(
                self._reconcile_leased(leased, profiles.get(connection_id)),
                name=f"ssh-host-reconcile-{connection_id}",
            )
            self._inflight[connection_id] = task
            task.add_done_callback(partial(self._forget_inflight, connection_id))

    def _forget_inflight(self, connection_id: str, _task: asyncio.Task[None]) -> None:
        self._inflight.pop(connection_id, None)

    async def _reconcile_leased(
        self,
        row: SshHostInstallation,
        profile: SshConnectionProfile | None,
    ) -> None:
        """Hold the lease alive for the duration of one reconciliation."""
        heartbeat = asyncio.create_task(
            self._renew_lease(row.connection_id),
            name=f"ssh-host-lease-{row.connection_id}",
        )
        try:
            await self._reconcile(row, profile)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def _renew_lease(self, connection_id: str) -> None:
        """Keep exclusive ownership while remote install commands are running."""
        while True:
            await asyncio.sleep(_LEASE_RENEW_SECONDS)
            renewed = await asyncio.to_thread(
                self.store.renew_lease,
                connection_id,
                lease_owner=self.worker_id,
                lease_seconds=_LEASE_SECONDS,
            )
            if not renewed:
                return

    async def _phase(self, row: SshHostInstallation, phase: str) -> None:
        changed = await asyncio.to_thread(
            self.store.set_phase,
            row.connection_id,
            lease_owner=self.worker_id,
            generation=row.generation,
            phase=phase,
        )
        if not changed:
            raise _ReconciliationSuperseded

    async def _reconcile(
        self,
        row: SshHostInstallation,
        profile: SshConnectionProfile | None,
    ) -> None:
        try:
            if row.desired_state == "detached" or profile is None:
                detached_profile = profile or SshConnectionProfile(
                    id=row.connection_id,
                    label=row.ssh_alias,
                    alias=row.ssh_alias,
                    created_at="",
                )
                await self.operations.detach(row.connection_id, detached_profile)
                await asyncio.to_thread(self.host_store.delete_host, row.host_id)
                changed = await asyncio.to_thread(
                    self.store.set_phase,
                    row.connection_id,
                    lease_owner=self.worker_id,
                    generation=row.generation,
                    phase="detached",
                    next_attempt_at=None,
                    release=True,
                )
                if not changed:
                    raise _ReconciliationSuperseded
                return
            if row.phase == "ready" and await asyncio.to_thread(
                self.host_store.is_online,
                row.host_id,
            ):
                changed = await asyncio.to_thread(
                    self.store.set_phase,
                    row.connection_id,
                    lease_owner=self.worker_id,
                    generation=row.generation,
                    phase="ready",
                    next_attempt_at=now_epoch() + _READY_RECHECK_SECONDS,
                    last_error=None,
                    release=True,
                )
                if not changed:
                    raise _ReconciliationSuperseded
                return
            await self._phase(row, "waiting_for_ssh")
            await self.operations.check_reachable(profile)
            await self._phase(row, "installing")
            await self.operations.ensure_installed(profile, row.bundle_version)
            await self._phase(row, "opening_tunnel")
            socket_path = await self.operations.ensure_tunnel(profile)
            await self._phase(row, "starting_host")
            token = secrets.token_urlsafe(32)
            host_name = f"{profile.label[:48]}-{profile.id[:8]}"
            await asyncio.to_thread(
                self.host_store.register_ssh_host,
                host_id=row.host_id,
                name=host_name,
                owner=row.owner,
                token=token,
                token_expires_at=now_epoch() + _TOKEN_TTL_SECONDS,
            )
            await self.operations.start_host(
                profile,
                host_id=row.host_id,
                host_name=host_name,
                token=token,
                socket_path=socket_path,
            )
            await self._phase(row, "waiting_for_host")
            deadline = asyncio.get_running_loop().time() + _HOST_READY_TIMEOUT_SECONDS
            while asyncio.get_running_loop().time() < deadline:
                if await asyncio.to_thread(self.host_store.is_online, row.host_id):
                    changed = await asyncio.to_thread(
                        self.store.set_phase,
                        row.connection_id,
                        lease_owner=self.worker_id,
                        generation=row.generation,
                        phase="ready",
                        next_attempt_at=now_epoch() + _READY_RECHECK_SECONDS,
                        last_error=None,
                        release=True,
                    )
                    if not changed:
                        raise _ReconciliationSuperseded
                    return
                await asyncio.sleep(1)
            raise TimeoutError("remote host did not become online before timeout")
        except _ReconciliationSuperseded:
            await asyncio.to_thread(
                self.store.release_lease,
                row.connection_id,
                lease_owner=self.worker_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - all stage failures enter durable backoff
            attempt = row.attempt + 1
            delay = min(_MAX_BACKOFF_SECONDS, 2 ** min(attempt, 10))
            _logger.warning("SSH host %s reconciliation failed: %s", row.connection_id, exc)
            await asyncio.to_thread(
                self.store.set_phase,
                row.connection_id,
                lease_owner=self.worker_id,
                generation=row.generation,
                phase="backoff",
                next_attempt_at=now_epoch() + delay,
                last_error=str(exc)[:4000],
                increment_attempt=True,
                release=True,
            )
