"""Persistent multiplexed SSH sessions for remote host aliases.

Uses OpenSSH ControlMaster so repeated commands to the same config Host
reuse one live connection instead of paying handshake cost every time.
A process-wide semaphore limits concurrent remote commands so pollers share
one narrow throttle.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from omnigent.ssh_connections_store import validate_ssh_alias

_logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 30.0
_DEFAULT_MAX_CONCURRENT_COMMANDS = 1
_CONTROL_PERSIST_S = 300
_CONTROL_DIR = Path.home() / ".omnigent" / "ssh" / "control"


def default_control_dir() -> Path:
    """Return the directory used for SSH multiplex control sockets."""
    return _CONTROL_DIR


def control_path_for_alias(alias: str, control_dir: Path | None = None) -> Path:
    """Return a stable control socket path for one SSH config Host alias."""
    digest = hashlib.sha256(alias.strip().encode()).hexdigest()[:16]
    return (control_dir or default_control_dir()) / f"{digest}.sock"


def ssh_multiplex_options(
    control_path: Path, *, connect_timeout_s: float = _DEFAULT_TIMEOUT_S
) -> list[str]:
    """Build OpenSSH options that enable connection multiplexing."""
    return [
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={int(connect_timeout_s)}",
        "-o",
        "ControlMaster=auto",
        "-o",
        f"ControlPath={control_path}",
        "-o",
        f"ControlPersist={_CONTROL_PERSIST_S}",
    ]


def build_ssh_command(
    alias: str,
    remote_command: str,
    *,
    control_path: Path,
    connect_timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> list[str]:
    """Build an ``ssh`` argv that multiplexes through *control_path*."""
    return [
        "ssh",
        *ssh_multiplex_options(control_path, connect_timeout_s=connect_timeout_s),
        alias.strip(),
        remote_command,
    ]


def build_ssh_close_command(alias: str, *, control_path: Path) -> list[str]:
    """Build an ``ssh -O exit`` argv that tears down a multiplex master."""
    return [
        "ssh",
        "-O",
        "exit",
        "-o",
        f"ControlPath={control_path}",
        alias.strip(),
    ]


@dataclass(frozen=True)
class SshPoolStats:
    """Runtime counters for the global SSH command throttle."""

    in_flight: int
    max_concurrent: int
    shutting_down: bool


class SshSession:
    """One multiplexed SSH session for a config Host alias."""

    def __init__(self, alias: str, *, control_dir: Path | None = None) -> None:
        error = validate_ssh_alias(alias)
        if error is not None:
            raise ValueError(error)
        self.alias = alias.strip()
        self._control_dir = control_dir or default_control_dir()
        self._control_path = control_path_for_alias(self.alias, self._control_dir)

    @property
    def control_path(self) -> Path:
        return self._control_path

    async def run(
        self,
        remote_command: str,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> tuple[int, bytes, bytes]:
        """Run one remote shell command over the multiplexed connection."""
        self._control_dir.mkdir(parents=True, exist_ok=True)
        cmd = build_ssh_command(
            self.alias,
            remote_command,
            control_path=self._control_path,
            connect_timeout_s=timeout_s,
        )
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"SSH command timed out for alias {self.alias!r}") from None
        return proc.returncode or 0, stdout, stderr

    async def close(self) -> None:
        """Close the multiplex master when one is active."""
        if not self._control_path.exists():
            return
        cmd = build_ssh_close_command(self.alias, control_path=self._control_path)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except TimeoutError:
            proc.kill()
            await proc.wait()
        except OSError:
            _logger.debug("Failed to close SSH multiplex for %s", self.alias, exc_info=True)


class SshSessionPool:
    """Reuses one :class:`SshSession` per SSH config Host alias."""

    def __init__(
        self,
        *,
        control_dir: Path | None = None,
        max_concurrent_commands: int = _DEFAULT_MAX_CONCURRENT_COMMANDS,
    ) -> None:
        self._control_dir = control_dir or default_control_dir()
        self._sessions: dict[str, SshSession] = {}
        self._session_lock = asyncio.Lock()
        self._command_semaphore = asyncio.Semaphore(max(1, max_concurrent_commands))
        self._max_concurrent = max(1, max_concurrent_commands)
        self._shutting_down = False
        self._in_flight = 0

    def session(self, alias: str) -> SshSession:
        """Return the pooled session for *alias*, creating it when needed."""
        trimmed = alias.strip()
        if trimmed not in self._sessions:
            self._sessions[trimmed] = SshSession(trimmed, control_dir=self._control_dir)
        return self._sessions[trimmed]

    async def _session_for_alias(self, alias: str) -> SshSession:
        trimmed = alias.strip()
        async with self._session_lock:
            session = self._sessions.get(trimmed)
            if session is None:
                session = SshSession(trimmed, control_dir=self._control_dir)
                self._sessions[trimmed] = session
            return session

    async def run(
        self,
        alias: str,
        remote_command: str,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> tuple[int, bytes, bytes]:
        """Run one remote command via the pooled session for *alias*."""
        if self._shutting_down:
            raise RuntimeError("SSH session pool is shutting down")
        async with self._command_semaphore:
            session = await self._session_for_alias(alias)
            self._in_flight += 1
            try:
                return await session.run(remote_command, timeout_s=timeout_s)
            finally:
                self._in_flight -= 1

    def stats(self) -> SshPoolStats:
        """Return runtime counters for observability."""
        return SshPoolStats(
            in_flight=self._in_flight,
            max_concurrent=self._max_concurrent,
            shutting_down=self._shutting_down,
        )

    async def close(self) -> None:
        """Close all pooled multiplex masters."""
        self._shutting_down = True
        while self._in_flight > 0:
            await asyncio.sleep(0.01)
        async with self._session_lock:
            for session in self._sessions.values():
                await session.close()
            self._sessions.clear()

    async def __aenter__(self) -> SshSessionPool:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()


_global_pool: SshSessionPool | None = None


def get_ssh_pool() -> SshSessionPool:
    """Return the process-wide pool of multiplexed SSH sessions."""
    global _global_pool
    if _global_pool is None:
        from omnigent.host.ssh_config import load_ssh_pool_config

        config = load_ssh_pool_config()
        _global_pool = SshSessionPool(
            max_concurrent_commands=config.max_concurrent_commands,
        )
    return _global_pool


async def shutdown_ssh_pool() -> None:
    """Close all pooled SSH masters (host daemon shutdown)."""
    global _global_pool
    if _global_pool is None:
        return
    await _global_pool.close()
    _global_pool = None


def reset_ssh_pool_for_tests(
    *,
    control_dir: Path | None = None,
    max_concurrent_commands: int = _DEFAULT_MAX_CONCURRENT_COMMANDS,
) -> SshSessionPool:
    """Replace the global pool (tests only)."""
    global _global_pool
    _global_pool = SshSessionPool(
        control_dir=control_dir,
        max_concurrent_commands=max_concurrent_commands,
    )
    return _global_pool


__all__ = [
    "SshPoolStats",
    "SshSession",
    "SshSessionPool",
    "build_ssh_close_command",
    "build_ssh_command",
    "control_path_for_alias",
    "default_control_dir",
    "get_ssh_pool",
    "reset_ssh_pool_for_tests",
    "shutdown_ssh_pool",
    "ssh_multiplex_options",
]
