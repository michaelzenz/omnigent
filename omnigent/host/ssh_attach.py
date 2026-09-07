"""SSH attach executor for the host daemon.

The desktop app is the only SSH executor: this module pulls the user's SSH
connection assignments from the server and converges them locally, using the
machine's own ``~/.ssh/config``, keys, and agent. Two deployment modes:

- **Local server** (embedded in the desktop app): the remote omnigent host
  daemon cannot reach a loopback-only server, so a reverse SSH tunnel forwards
  a remote Unix socket back to the local server's TCP listener.
- **Remote server**: no tunnel — a preflight check verifies the SSH box can
  reach the server URL directly, and the remote daemon connects to that URL.

Every phase transition and log entry is pushed back to the server so the
settings UI renders the same timeline it did when the server executed SSH.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shlex
import shutil
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from urllib.parse import urlparse

from omnigent.host.polling.context import PollContext
from omnigent.onboarding.harness_install import PI_KEY, harness_install_spec
from omnigent.ssh_remote import ssh_run

_logger = logging.getLogger(__name__)

_LEASE_SECONDS = 30
_LEASE_RENEW_SECONDS = 10
_READY_RECHECK_SECONDS = 15
_HOST_READY_TIMEOUT_SECONDS = 90
_MAX_BACKOFF_SECONDS = 15 * 60
_POLL_INTERVAL_S = 2.0

CommandRunner = Callable[[list[str], float], Awaitable[tuple[int, bytes, bytes]]]
InstallCommandBuilder = Callable[..., str]
LogSink = Callable[..., None]


def _now() -> int:
    return int(time.time())


@dataclass(frozen=True)
class SshAssignment:
    """One connection row as delivered by the server's assignments API."""

    connection_id: str
    label: str
    alias: str
    desired_state: str
    phase: str
    generation: int
    attempt: int
    next_attempt_at: int | None
    lease_owner: str | None
    lease_expires_at: int | None
    host_id: str
    bundle_version: str


@dataclass(frozen=True)
class SshAttachSettings:
    """Per-user install settings the executor applies on remote hosts."""

    package_index_url: str | None
    npm_registry_url: str | None
    remote_namespace: str


def _pi_npm_package() -> str:
    """Return Pi's canonical npm package from shared harness metadata."""
    spec = harness_install_spec(PI_KEY)
    if spec is None or spec.package is None:
        raise RuntimeError("Pi has no npm install package configured")
    return spec.package


def build_install_command(
    version: str,
    package_spec: str | None = None,
    bundle_sha256: str | None = None,
    index_url: str | None = None,
    find_links: str | None = None,
    npm_registry_url: str | None = None,
    remote_namespace: str = "",
) -> str:
    """Build the idempotent remote installation command."""
    quoted_version = shlex.quote(version)
    quoted_package_spec = shlex.quote(package_spec or f"omnigent=={version}")
    index_env = f"UV_INDEX_URL={shlex.quote(index_url)} " if index_url else ""
    find_links_arg = f"--find-links {shlex.quote(find_links)} " if find_links else ""
    npm_registry_arg = f" --registry {shlex.quote(npm_registry_url)}" if npm_registry_url else ""
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
        f'root="$HOME/.omnigent/host/{shlex.quote(remote_namespace)}"; '
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
        'rm -f "$root/current"; mv "$link" "$root/current"; '
        f"pi_spec={shlex.quote(_pi_npm_package())}; "
        f"pi_registry={shlex.quote(npm_registry_url or '')}; "
        'pi_root="$root/harnesses/pi"; '
        'pi_bin="$pi_root/node_modules/.bin/pi"; '
        'if [ ! -x "$pi_bin" ] || '
        '[ "$(cat "$pi_root/.package-spec" 2>/dev/null || true)" != "$pi_spec" ] || '
        '[ "$(cat "$pi_root/.registry-url" 2>/dev/null || true)" != "$pi_registry" ]; then '
        "command -v npm >/dev/null 2>&1 || "
        '{ echo "npm is required to install Pi on the SSH host" >&2; exit 1; }; '
        f'mkdir -p "$pi_root"; npm install --prefix "$pi_root"{npm_registry_arg} "$pi_spec"; '
        'printf %s "$pi_spec" > "$pi_root/.package-spec"; '
        'printf %s "$pi_registry" > "$pi_root/.registry-url"; fi'
    )


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


_WHEEL_BUILD_EXCLUDE_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".omnigent",
    }
)


def _newest_source_mtime(root: Path, exclude: frozenset[str]) -> float:
    """Newest mtime among files under root, skipping generated/build dirs."""
    newest = 0.0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in exclude]
        for name in filenames:
            try:
                mtime = Path(dirpath, name).stat().st_mtime
            except OSError:
                continue
            if mtime > newest:
                newest = mtime
    return newest


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


def url_is_loopback(url: str) -> bool:
    """Whether *url* points at this machine (the server is embedded)."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in {"localhost", "127.0.0.1", "::1"}


def loopback_tunnel_target(server_url: str) -> tuple[str, int] | None:
    """TCP (host, port) a reverse tunnel should forward to, for a loopback server.

    :returns: ``None`` when the server URL is not loopback (remote-server
        mode needs no tunnel) or carries no usable port.
    """
    if not url_is_loopback(server_url):
        return None
    parsed = urlparse(server_url)
    port = parsed.port
    if port is None:
        return None
    return (parsed.hostname or "127.0.0.1", port)


def alias_profile(alias: str):
    """Build the minimal profile the shared SSH pool keys on: the alias."""
    from omnigent.entities import SshConnectionProfile

    return SshConnectionProfile(
        id=hashlib.sha256(alias.encode()).hexdigest()[:16],
        label=alias,
        alias=alias,
        created_at="",
    )


class SshHostOperations:
    """SSH mechanics against one remote machine, injectable for tests."""

    def __init__(
        self,
        *,
        remote_namespace: str,
        settings: SshAttachSettings,
        server_url: str,
        tunnel_target: tuple[str, int] | None,
        command_runner: CommandRunner = _run_local_command,
        install_command_builder: InstallCommandBuilder = build_install_command,
        control_dir: Path | None = None,
        log: LogSink | None = None,
    ) -> None:
        self._settings = settings
        self._server_url = server_url.rstrip("/")
        self._tunnel_target = tunnel_target
        self._run = command_runner
        self._install_command_builder = install_command_builder
        self._remote_namespace = remote_namespace
        self._control_dir = control_dir or Path.home() / ".omnigent" / "ssh" / remote_namespace
        self._bundle_dir = self._control_dir / "bundles"
        self._local_bundles: dict[str, list[Path] | None] = {}
        self._log = log or (
            lambda connection_id, *, phase, level, message: None  # noqa: ARG005
        )

    @property
    def needs_tunnel(self) -> bool:
        """Whether the remote daemon needs a reverse tunnel to reach the server."""
        return self._tunnel_target is not None

    async def remote_home(self, alias: str) -> str:
        """Resolve the remote account's absolute home directory."""
        code, stdout, stderr = await ssh_run(
            alias_profile(alias), 'printf "%s" "$HOME"', timeout_s=15
        )
        if code != 0:
            raise RuntimeError(
                (stderr or stdout).decode().strip() or "could not resolve remote home"
            )
        home = stdout.decode().strip()
        if not home.startswith("/"):
            raise RuntimeError("remote HOME is not an absolute path")
        return home

    async def remote_socket(self, alias: str, connection_id: str) -> str:
        """Build the absolute remote socket path for the reverse forward.

        OpenSSH doesn't shell-expand ``~`` in stream-local ``-R`` paths, so the
        remote home has to be resolved before constructing the forward.
        """
        return (
            f"{await self.remote_home(alias)}/.omnigent/"
            f"server-{self._remote_namespace}-{connection_id}.sock"
        )

    def _control_path(self, connection_id: str, alias: str) -> Path:
        identity = f"{connection_id}\0{alias}\0{self._server_url}"
        digest = hashlib.sha256(identity.encode()).hexdigest()[:20]
        return self._control_dir / f"{digest}.sock"

    async def check_reachable(self, connection_id: str, alias: str) -> None:
        self._log(
            connection_id,
            phase="waiting_for_ssh",
            level="info",
            message=f"Probing SSH connection to {alias}...",
        )
        code, stdout, stderr = await ssh_run(alias_profile(alias), "true", timeout_s=15)
        if code != 0:
            error_msg = (stderr or stdout).decode().strip() or "SSH is unreachable"
            self._log(
                connection_id,
                phase="waiting_for_ssh",
                level="error",
                message=f"SSH unreachable: {error_msg}",
            )
            raise RuntimeError(error_msg)
        self._log(
            connection_id,
            phase="waiting_for_ssh",
            level="info",
            message="SSH connection established",
        )

    async def ensure_installed(self, connection_id: str, alias: str, version: str) -> None:
        self._log(
            connection_id,
            phase="installing",
            level="info",
            message=f"Checking remote installation for version {version}...",
        )
        package_spec: str | None = None
        bundle_sha256: str | None = None
        find_links: str | None = None
        local_bundles = await self._local_bundle(version)
        if local_bundles is not None:
            main_bundle = _main_wheel(local_bundles)
            remote_package_dir = (
                f"{await self.remote_home(alias)}/.omnigent/host/{self._remote_namespace}/packages"
            )
            bundle_hashes = [
                await asyncio.to_thread(_file_sha256, bundle) for bundle in local_bundles
            ]
            bundle_sha256 = hashlib.sha256("".join(bundle_hashes).encode()).hexdigest()
            package_spec = f"{remote_package_dir}/{main_bundle.name}"
            find_links = remote_package_dir
            if not await self._remote_bundle_matches(alias, version, bundle_sha256):
                self._log(
                    connection_id,
                    phase="installing",
                    level="info",
                    message=f"Uploading {len(local_bundles)} wheel(s) to remote host...",
                )
                await self._upload_bundles(connection_id, alias, local_bundles, remote_package_dir)
                self._log(
                    connection_id,
                    phase="installing",
                    level="info",
                    message="Wheel upload complete",
                )
            else:
                self._log(
                    connection_id,
                    phase="installing",
                    level="info",
                    message="Remote already has matching wheels, skipping upload",
                )
        install_command = self._install_command_builder(
            version,
            package_spec,
            bundle_sha256,
            self._settings.package_index_url,
            find_links,
            self._settings.npm_registry_url,
            self._remote_namespace,
        )
        command = f"${{SHELL:-bash}} -l -c {shlex.quote(install_command)}"
        self._log(
            connection_id,
            phase="installing",
            level="info",
            message="Running remote installation (uv pip install, npm install Pi)...",
        )
        code, stdout, stderr = await ssh_run(alias_profile(alias), command, timeout_s=600)
        if code != 0:
            error_msg = (stderr or stdout).decode().strip() or "remote install failed"
            self._log(
                connection_id,
                phase="installing",
                level="error",
                message=f"Installation failed: {error_msg}",
            )
            raise RuntimeError(error_msg)
        stdout_text = stdout.decode().strip()
        if stdout_text:
            self._log(
                connection_id,
                phase="installing",
                level="info",
                message=f"Install output: {stdout_text[:500]}",
            )
        self._log(
            connection_id,
            phase="installing",
            level="info",
            message=f"Remote installation completed (version {version})",
        )

    async def check_server_reachable(self, connection_id: str, alias: str) -> None:
        """Preflight: can the SSH box reach the server URL directly?

        Only used in remote-server mode. Failing here is a clear
        network/VPN problem, not a cryptic daemon connect timeout.
        """
        parsed = urlparse(self._server_url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self._log(
            connection_id,
            phase="preflight",
            level="info",
            message=f"Checking that {alias} can reach the server at {host}:{port}...",
        )
        probe = (
            "bash -c "
            + shlex.quote(f"exec 3<>/dev/tcp/{shlex.quote(host)}/{port}")
            + " 2>/dev/null && echo reachable || echo unreachable"
        )
        code, stdout, stderr = await ssh_run(alias_profile(alias), probe, timeout_s=20)
        outcome = stdout.decode().strip()
        if code != 0 or outcome != "reachable":
            detail = (stderr or stdout).decode().strip()
            message = (
                f"Remote machine cannot reach the server at {host}:{port} — "
                "check VPN/network reachability from the SSH host."
            )
            self._log(
                connection_id,
                phase="preflight",
                level="error",
                message=message + (f" ({detail})" if detail else ""),
            )
            raise RuntimeError(message)
        self._log(
            connection_id,
            phase="preflight",
            level="info",
            message="Server is reachable from the remote machine",
        )

    async def _remote_bundle_matches(self, alias: str, version: str, bundle_sha256: str) -> bool:
        """Report whether the remote already holds this exact wheel bundle."""
        command = (
            f'root="$HOME/.omnigent/host/{self._remote_namespace}"; '
            f"version={shlex.quote(version)}; "
            'target="$root/versions/$version"; '
            '[ -f "$target/.complete" ] && '
            '[ "$(cat "$target/.bundle-sha256" 2>/dev/null || true)" = '
            f"{shlex.quote(bundle_sha256)} ]"
        )
        code, _, _ = await ssh_run(alias_profile(alias), command, timeout_s=15)
        return code == 0

    async def _upload_bundles(
        self,
        connection_id: str,
        alias: str,
        local_bundles: list[Path],
        remote_package_dir: str,
    ) -> None:
        code, stdout, stderr = await ssh_run(
            alias_profile(alias), f"mkdir -p {shlex.quote(remote_package_dir)}", timeout_s=30
        )
        if code != 0:
            raise RuntimeError(
                (stderr or stdout).decode().strip() or "remote package directory failed"
            )
        for local_bundle in local_bundles:
            remote_bundle = f"{remote_package_dir}/{local_bundle.name}"
            self._log(
                connection_id,
                phase="installing",
                level="info",
                message=f"Uploading {local_bundle.name}...",
            )
            code, stdout, stderr = await self._run(
                [
                    "scp",
                    "-q",
                    str(local_bundle),
                    f"{alias}:{shlex.quote(remote_bundle)}",
                ],
                300,
            )
            if code != 0:
                error_msg = (stderr or stdout).decode().strip() or "wheel upload failed"
                self._log(
                    connection_id,
                    phase="installing",
                    level="error",
                    message=f"Upload failed for {local_bundle.name}: {error_msg}",
                )
                raise RuntimeError(error_msg)

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
        projects = [
            source_root / "sdks" / "python-client",
            source_root / "sdks" / "ui",
            source_root,
        ]
        existing = sorted(output_dir.glob("*.whl"))
        if len(existing) >= len(projects):
            oldest_wheel = min(w.stat().st_mtime for w in existing)
            if _newest_source_mtime(source_root, _WHEEL_BUILD_EXCLUDE_DIRS) <= oldest_wheel:
                self._local_bundles[version] = existing
                return existing
        for stale in output_dir.glob("*.whl"):
            stale.unlink()
        build_prefix = [uv]
        index_url = self._settings.package_index_url
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

    async def ensure_tunnel(self, connection_id: str, alias: str) -> str:
        if self._tunnel_target is None:
            raise RuntimeError("reverse tunnel requested without a local server target")
        self._log(
            connection_id,
            phase="opening_tunnel",
            level="info",
            message="Establishing SSH reverse tunnel...",
        )
        self._control_dir.mkdir(parents=True, exist_ok=True)
        control_path = self._control_path(connection_id, alias)
        check = ["ssh", "-S", str(control_path), "-O", "check", alias]
        code, _, _ = await self._run(check, 10)
        if code == 0:
            await self._run(["ssh", "-S", str(control_path), "-O", "exit", alias], 10)
        with suppress(FileNotFoundError):
            control_path.unlink()
        remote_socket = await self.remote_socket(alias, connection_id)
        code, stdout, stderr = await ssh_run(
            alias_profile(alias), f"rm -f {shlex.quote(remote_socket)}", timeout_s=15
        )
        if code != 0:
            raise RuntimeError(
                (stderr or stdout).decode().strip() or "failed to remove stale remote socket"
            )
        tunnel_host, tunnel_port = self._tunnel_target
        reverse = f"{remote_socket}:{tunnel_host}:{tunnel_port}"
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
            alias,
        ]
        code, stdout, stderr = await self._run(start, 30)
        if code != 0:
            error_msg = (stderr or stdout).decode().strip() or "reverse tunnel failed"
            self._log(
                connection_id,
                phase="opening_tunnel",
                level="error",
                message=f"Tunnel failed: {error_msg}",
            )
            raise RuntimeError(error_msg)
        code, stdout, stderr = await ssh_run(
            alias_profile(alias), f"test -S {shlex.quote(remote_socket)}", timeout_s=15
        )
        if code != 0:
            error_msg = (
                stderr or stdout
            ).decode().strip() or "reverse tunnel did not create the remote Unix socket"
            self._log(
                connection_id,
                phase="opening_tunnel",
                level="error",
                message=f"Tunnel socket missing: {error_msg}",
            )
            raise RuntimeError(error_msg)
        self._log(
            connection_id,
            phase="opening_tunnel",
            level="info",
            message=f"Reverse tunnel established at {remote_socket}",
        )
        return remote_socket

    async def start_host(
        self,
        connection_id: str,
        alias: str,
        *,
        host_id: str,
        host_name: str,
        token: str,
        socket_path: str | None,
    ) -> None:
        self._log(
            connection_id,
            phase="starting_host",
            level="info",
            message=f"Starting remote host daemon ({host_name})...",
        )
        values = {
            "OMNIGENT_HOST_TOKEN": token,
            "OMNIGENT_HOST_ID": host_id,
            "OMNIGENT_HOST_NAME": host_name,
            # Remote shells (e.g. Arca) may set OMNIGENT_REQUIRE_WRAPPER=1;
            # bypass it so the host daemon can launch directly.
            "OMNIGENT_WRAPPER_BYPASS": "1",
        }
        env = " ".join(f"{key}={shlex.quote(value)}" for key, value in values.items())
        runtime_name = shlex.quote(f"{self._remote_namespace}-{connection_id}")
        if socket_path is not None:
            server_args = (
                f"--server http://localhost --server-unix-socket {shlex.quote(socket_path)}"
            )
        else:
            server_args = f"--server {shlex.quote(self._server_url)}"
        # Run through the user's login shell so the daemon inherits the same
        # PATH they see in an interactive session — git, nvm, asdf, Homebrew,
        # etc. Without this the non-interactive SSH session only has the
        # minimal default PATH (often just /usr/bin:/bin).
        inner = (
            f'set -eu; root="$HOME/.omnigent/host/{self._remote_namespace}"; '
            f'runtime="$root/runtimes"/{runtime_name}; mkdir -p "$runtime"; '
            'pi_path="$root/harnesses/pi/node_modules/.bin"; '
            'if [ -f "$runtime/host.pid" ]; then '
            'pid="$(cat "$runtime/host.pid" 2>/dev/null || true)"; '
            'if [ -n "$pid" ] && ps -p "$pid" -o command= 2>/dev/null '
            '| grep -F "$root/current/venv/bin/omnigent host" >/dev/null; '
            'then kill "$pid" 2>/dev/null || true; '
            # Wait for the old daemon to exit so the server marks it
            # offline before the new one connects — otherwise the
            # reconciler sees the stale connection as "online" and
            # short-circuits before the new daemon is ready.
            "for i in 1 2 3 4 5; do "
            'if ! kill -0 "$pid" 2>/dev/null; then break; fi; sleep 0.5; done; fi; fi; '
            f'nohup env PATH="$pi_path:$PATH" {env} "$root/current/venv/bin/omnigent" host '
            f"{server_args} "
            '--non-interactive >"$runtime/host.log" 2>&1 < /dev/null & '
            'echo "$!" >"$runtime/host.pid"'
        )
        command = f"${{SHELL:-bash}} -l -c {shlex.quote(inner)}"
        code, stdout, stderr = await ssh_run(alias_profile(alias), command, timeout_s=30)
        if code != 0:
            error_msg = (stderr or stdout).decode().strip() or "remote host start failed"
            self._log(
                connection_id,
                phase="starting_host",
                level="error",
                message=f"Host start failed: {error_msg}",
            )
            raise RuntimeError(error_msg)
        self._log(
            connection_id,
            phase="starting_host",
            level="info",
            message="Remote host daemon started",
        )

    async def detach(self, connection_id: str, alias: str) -> None:
        control_path = self._control_path(connection_id, alias)
        await self._run(["ssh", "-S", str(control_path), "-O", "exit", alias], 10)
        with suppress(FileNotFoundError):
            control_path.unlink()
        runtime_name = shlex.quote(f"{self._remote_namespace}-{connection_id}")
        command = (
            f'root="$HOME/.omnigent/host/{self._remote_namespace}"; '
            f'runtime="$root/runtimes"/{runtime_name}; '
            'if [ -f "$runtime/host.pid" ]; then '
            'pid="$(cat "$runtime/host.pid" 2>/dev/null || true)"; '
            'if [ -n "$pid" ] && ps -p "$pid" -o command= 2>/dev/null '
            '| grep -F "$root/current/venv/bin/omnigent host" >/dev/null; '
            'then kill "$pid" 2>/dev/null || true; fi; '
            'rm -f "$runtime/host.pid"; fi; '
            f'rm -f "$HOME/.omnigent/server-{self._remote_namespace}-{connection_id}.sock"'
        )
        with suppress(Exception):
            await ssh_run(alias_profile(alias), command, timeout_s=15)


class _Superseded(Exception):
    """Durable intent changed while this executor handled an older generation."""


class SshAttachExecutor:
    """Polls the server for the owner's SSH connections and converges them."""

    def __init__(
        self,
        *,
        operations: SshHostOperations | None = None,
        poll_interval_s: float = _POLL_INTERVAL_S,
    ) -> None:
        self._operations_override = operations
        self._poll_interval_s = poll_interval_s
        self._inflight: dict[str, asyncio.Task[None]] = {}
        self._operations: SshHostOperations | None = None

    # ── PollSource protocol ────────────────────────────────

    @property
    def name(self) -> str:
        return "ssh_attach"

    def enabled(self, ctx: PollContext) -> bool:  # noqa: ARG002
        return True

    def interval_s(self, ctx: PollContext) -> float:  # noqa: ARG002
        return self._poll_interval_s

    @property
    def read_only(self) -> bool:
        return False

    async def on_start(self, ctx: PollContext) -> None:  # noqa: ARG002
        return None

    async def on_stop(self) -> None:
        inflight = list(self._inflight.values())
        for task in inflight:
            task.cancel()
        for task in inflight:
            with suppress(asyncio.CancelledError, Exception):
                await task
        self._inflight.clear()

    # ── Polling ────────────────────────────────────────────

    async def poll_once(self, ctx: PollContext) -> None:
        tunnel_target = loopback_tunnel_target(ctx.server_url)
        if url_is_loopback(ctx.server_url) and tunnel_target is None:
            # A loopback server without a usable port cannot be reverse-tunneled;
            # falling through would preflight against a default port and fail
            # with a misleading network error.
            _logger.warning(
                "Server URL %s is loopback but has no port; cannot attach SSH hosts",
                ctx.server_url,
            )
            return
        try:
            payload = await self._get(ctx, "/v1/host/ssh/assignments")
        except Exception as exc:  # noqa: BLE001 — next poll retries
            _logger.debug("SSH assignments pull failed: %s", exc)
            return
        raw_settings = payload.get("settings") or {}
        settings = SshAttachSettings(
            package_index_url=raw_settings.get("package_index_url"),
            npm_registry_url=raw_settings.get("npm_registry_url"),
            remote_namespace=raw_settings.get("remote_namespace") or "",
        )
        server_version = str(payload.get("server_version") or "")
        self._operations = self._operations_override or SshHostOperations(
            remote_namespace=settings.remote_namespace,
            settings=settings,
            server_url=ctx.server_url,
            tunnel_target=tunnel_target,
            log=self._executor_log(ctx),
        )
        now = _now()
        for row in payload.get("connections") or []:
            assignment = SshAssignment(
                connection_id=str(row.get("connection_id") or ""),
                label=str(row.get("label") or ""),
                alias=str(row.get("alias") or ""),
                desired_state=str(row.get("desired_state") or "connected"),
                phase=str(row.get("phase") or "queued"),
                generation=int(row.get("generation") or 0),
                attempt=int(row.get("attempt") or 0),
                next_attempt_at=row.get("next_attempt_at"),
                lease_owner=row.get("lease_owner"),
                lease_expires_at=row.get("lease_expires_at"),
                host_id=str(row.get("host_id") or ""),
                bundle_version=str(row.get("bundle_version") or server_version),
            )
            if not assignment.connection_id or assignment.connection_id in self._inflight:
                continue
            if not self._is_due(ctx, assignment, now):
                continue
            task = asyncio.create_task(
                self._reconcile(ctx, assignment),
                name=f"ssh-attach-{assignment.connection_id}",
            )
            self._inflight[assignment.connection_id] = task
            task.add_done_callback(partial(self._forget, assignment.connection_id))

    def _executor_log(self, ctx: PollContext) -> LogSink:
        def _log(connection_id: str, *, phase: str, level: str, message: str) -> None:
            asyncio.get_running_loop().create_task(
                self._push_log(ctx, connection_id, phase, level, message)
            )

        return _log

    def _is_due(self, ctx: PollContext, row: SshAssignment, now: int) -> bool:
        if row.desired_state == "detached":
            # Detached rows only need work while their cleanup is unfinished.
            return row.phase != "detached"
        if row.next_attempt_at is not None and row.next_attempt_at > now:
            return False
        if row.lease_owner is not None and row.lease_expires_at is not None:
            if row.lease_expires_at > now and row.lease_owner != ctx.host_id:
                return False
        return True

    def _forget(self, connection_id: str, _task: asyncio.Task[None]) -> None:
        self._inflight.pop(connection_id, None)

    # ── Reconciliation ─────────────────────────────────────

    async def _reconcile(self, ctx: PollContext, row: SshAssignment) -> None:
        assert self._operations is not None
        ops = self._operations
        connection_id = row.connection_id
        heartbeat = asyncio.create_task(
            self._renew_lease(ctx, connection_id),
            name=f"ssh-attach-lease-{connection_id}",
        )
        try:
            claimed = await self._claim(ctx, connection_id)
            if not claimed.get("claimed"):
                return
            generation = int(claimed.get("generation") or row.generation)
            install_version = str(claimed.get("bundle_version") or row.bundle_version)
            desired_state = str(claimed.get("desired_state") or row.desired_state)
            if desired_state != "connected":
                await self._detach(ctx, ops, row)
                return
            if row.phase == "ready" and bool(claimed.get("remote_host_online")):
                await self._phase(
                    ctx,
                    connection_id,
                    generation,
                    "ready",
                    next_attempt_at=_now() + _READY_RECHECK_SECONDS,
                    last_error=None,
                    release=True,
                )
                return
            await self._phase(ctx, connection_id, generation, "waiting_for_ssh")
            await ops.check_reachable(connection_id, row.alias)
            await self._phase(ctx, connection_id, generation, "installing")
            await ops.ensure_installed(connection_id, row.alias, install_version)
            socket_path: str | None = None
            if ops.needs_tunnel:
                await self._phase(ctx, connection_id, generation, "opening_tunnel")
                socket_path = await ops.ensure_tunnel(connection_id, row.alias)
            else:
                await self._phase(ctx, connection_id, generation, "preflight")
                await ops.check_server_reachable(connection_id, row.alias)
            await self._phase(ctx, connection_id, generation, "starting_host")
            registered = await self._register_remote_host(ctx, connection_id)
            await ops.start_host(
                connection_id,
                row.alias,
                host_id=str(registered["host_id"]),
                host_name=str(registered["name"]),
                token=str(registered["token"]),
                socket_path=socket_path,
            )
            await self._phase(ctx, connection_id, generation, "waiting_for_host")
            await self._wait_for_remote_host(ctx, connection_id, generation)
        except _Superseded:
            with suppress(Exception):
                await ctx.client.post(
                    f"/v1/host/ssh/connections/{connection_id}/release-lease", json={}
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — all stage failures enter durable backoff
            attempt = row.attempt + 1
            delay = min(_MAX_BACKOFF_SECONDS, 2 ** min(attempt, 10))
            _logger.warning("SSH attach %s reconciliation failed: %s", connection_id, exc)
            await self._push_log(ctx, connection_id, "backoff", "error", str(exc))
            await self._phase(
                ctx,
                connection_id,
                row.generation,
                "backoff",
                next_attempt_at=_now() + delay,
                last_error=str(exc)[:4000],
                increment_attempt=True,
                release=True,
            )
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def _detach(self, ctx: PollContext, ops: SshHostOperations, row: SshAssignment) -> None:
        connection_id = row.connection_id
        await self._push_log(ctx, connection_id, "detaching", "info", "Detaching remote host...")
        with suppress(Exception):
            await ops.detach(connection_id, row.alias)
        with suppress(Exception):
            await self._delete_remote_host(ctx, connection_id)
        await self._phase(
            ctx, connection_id, row.generation, "detached", next_attempt_at=None, release=True
        )
        await self._push_log(ctx, connection_id, "detached", "info", "Remote host detached")

    async def _renew_lease(self, ctx: PollContext, connection_id: str) -> None:
        """Keep exclusive ownership while remote install commands are running."""
        while True:
            await asyncio.sleep(_LEASE_RENEW_SECONDS)
            await self._claim(ctx, connection_id)

    async def _wait_for_remote_host(
        self, ctx: PollContext, connection_id: str, generation: int
    ) -> None:
        deadline = asyncio.get_running_loop().time() + _HOST_READY_TIMEOUT_SECONDS
        # If the old daemon was killed, wait for the server to see it
        # go offline before accepting "online" as the new daemon.
        saw_offline = not await self._remote_online(ctx, connection_id)
        while asyncio.get_running_loop().time() < deadline:
            online = await self._remote_online(ctx, connection_id)
            if not online:
                saw_offline = True
            if online and saw_offline:
                await self._phase(
                    ctx,
                    connection_id,
                    generation,
                    "ready",
                    next_attempt_at=_now() + _READY_RECHECK_SECONDS,
                    last_error=None,
                    release=True,
                )
                await self._push_log(
                    ctx, connection_id, "ready", "info", "Host is online and ready"
                )
                return
            await asyncio.sleep(1)
        raise TimeoutError("remote host did not become online before timeout")

    # ── Server API ─────────────────────────────────────────

    async def _get(self, ctx: PollContext, path: str) -> dict:
        response = await ctx.client.get(path)
        response.raise_for_status()
        return response.json()

    async def _post(self, ctx: PollContext, path: str, json: dict) -> dict:
        response = await ctx.client.post(path, json=json)
        response.raise_for_status()
        return response.json()

    async def _delete(self, ctx: PollContext, path: str) -> dict:
        response = await ctx.client.delete(path)
        response.raise_for_status()
        return response.json()

    async def _claim(self, ctx: PollContext, connection_id: str) -> dict:
        return await self._post(
            ctx,
            f"/v1/host/ssh/connections/{connection_id}/claim",
            {"lease_seconds": _LEASE_SECONDS},
        )

    async def _phase(
        self,
        ctx: PollContext,
        connection_id: str,
        generation: int,
        phase: str,
        *,
        next_attempt_at: int | None = None,
        last_error: str | None = None,
        increment_attempt: bool = False,
        release: bool = False,
    ) -> None:
        result = await self._post(
            ctx,
            f"/v1/host/ssh/connections/{connection_id}/phase",
            {
                "generation": generation,
                "phase": phase,
                "next_attempt_at": next_attempt_at,
                "last_error": last_error,
                "increment_attempt": increment_attempt,
                "release": release,
            },
        )
        if result.get("superseded"):
            raise _Superseded()

    async def _push_log(
        self, ctx: PollContext, connection_id: str, phase: str, level: str, message: str
    ) -> None:
        try:
            await self._post(
                ctx,
                f"/v1/host/ssh/connections/{connection_id}/logs",
                {"entries": [{"phase": phase, "level": level, "message": message}]},
            )
        except Exception:
            _logger.debug("SSH log push failed for %s", connection_id, exc_info=True)

    async def _register_remote_host(self, ctx: PollContext, connection_id: str) -> dict:
        return await self._post(
            ctx, f"/v1/host/ssh/connections/{connection_id}/register-remote-host", {}
        )

    async def _delete_remote_host(self, ctx: PollContext, connection_id: str) -> None:
        await self._delete(ctx, f"/v1/host/ssh/connections/{connection_id}/remote-host")

    async def _remote_online(self, ctx: PollContext, connection_id: str) -> bool:
        result = await self._get(
            ctx, f"/v1/host/ssh/connections/{connection_id}/remote-host-status"
        )
        return bool(result.get("online"))
