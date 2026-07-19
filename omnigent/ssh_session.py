"""Persistent multiplexed SSH sessions for remote host aliases.

Uses OpenSSH ControlMaster so repeated commands to the same config Host
reuse one live connection instead of paying handshake cost every time.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

from omnigent.ssh_connections_store import validate_ssh_alias

_logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 30.0
_CONTROL_PERSIST_S = 300
_CONTROL_DIR = Path.home() / ".omnigent" / "ssh" / "control"


def default_control_dir() -> Path:
    """Return the directory used for SSH multiplex control sockets."""
    return _CONTROL_DIR


def control_path_for_alias(alias: str, control_dir: Path | None = None) -> Path:
    """Return a stable control socket path for one SSH config Host alias."""
    digest = hashlib.sha256(alias.strip().encode()).hexdigest()[:16]
    return (control_dir or default_control_dir()) / f"{digest}.sock"


def ssh_multiplex_options(control_path: Path, *, connect_timeout_s: float = _DEFAULT_TIMEOUT_S) -> list[str]:
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

    def __init__(self, *, control_dir: Path | None = None) -> None:
        self._control_dir = control_dir or default_control_dir()
        self._sessions: dict[str, SshSession] = {}

    def session(self, alias: str) -> SshSession:
        """Return the pooled session for *alias*, creating it when needed."""
        trimmed = alias.strip()
        if trimmed not in self._sessions:
            self._sessions[trimmed] = SshSession(trimmed, control_dir=self._control_dir)
        return self._sessions[trimmed]

    async def run(
        self,
        alias: str,
        remote_command: str,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> tuple[int, bytes, bytes]:
        """Run one remote command via the pooled session for *alias*."""
        return await self.session(alias).run(remote_command, timeout_s=timeout_s)

    async def close(self) -> None:
        """Close all pooled multiplex masters."""
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
        _global_pool = SshSessionPool()
    return _global_pool


async def shutdown_ssh_pool() -> None:
    """Close all pooled SSH masters (host daemon shutdown)."""
    global _global_pool
    if _global_pool is None:
        return
    await _global_pool.close()
    _global_pool = None


def reset_ssh_pool_for_tests(*, control_dir: Path | None = None) -> SshSessionPool:
    """Replace the global pool (tests only)."""
    global _global_pool
    _global_pool = SshSessionPool(control_dir=control_dir)
    return _global_pool


__all__ = [
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
