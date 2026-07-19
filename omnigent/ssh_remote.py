"""Remote file operations over SSH config Host aliases."""

from __future__ import annotations

import asyncio
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path

from omnigent.ssh_connections_store import validate_ssh_alias

_DEFAULT_TIMEOUT_S = 30.0
_DEFAULT_REMOTE_CODEX_HOME = "$HOME/.codex"


def _remote_codex_home(codex_home: str) -> str:
    """Return a remote-shell path for Codex home (tilde must not be quoted)."""
    if codex_home in {"~/.codex", "~"}:
        return _DEFAULT_REMOTE_CODEX_HOME
    if codex_home.startswith("~/"):
        return f"$HOME/{codex_home[2:]}"
    return codex_home


@dataclass(frozen=True)
class RemoteCodexRollout:
    """One Codex rollout path discovered on a remote host."""

    path: str
    mtime_ms: int


def _ssh_base_options() -> list[str]:
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={int(_DEFAULT_TIMEOUT_S)}",
    ]


async def ssh_run(alias: str, remote_command: str, *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> tuple[int, bytes, bytes]:
    """Run one remote shell command via an SSH config alias."""
    if validate_ssh_alias(alias) is not None:
        raise ValueError(f"Invalid SSH alias: {alias!r}")
    cmd = [*_ssh_base_options(), alias.strip(), remote_command]
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
        raise TimeoutError(f"SSH command timed out for alias {alias!r}") from None
    return proc.returncode or 0, stdout, stderr


async def ssh_remote_file_bytes(alias: str, remote_path: str, *, byte_offset: int = 0) -> bytes:
    """Read a remote file from *byte_offset* onward."""
    quoted = shlex.quote(remote_path)
    if byte_offset <= 0:
        remote_cmd = f"cat {quoted}"
    else:
        remote_cmd = f"tail -c +{byte_offset + 1} {quoted}"
    code, stdout, stderr = await ssh_run(alias, remote_cmd)
    if code != 0:
        message = stderr.decode().strip() or stdout.decode().strip() or "remote read failed"
        raise OSError(message)
    return stdout


async def ssh_remote_file_size(alias: str, remote_path: str) -> int:
    """Return the byte size of a remote file."""
    quoted = shlex.quote(remote_path)
    remote_cmd = f"wc -c < {quoted}"
    code, stdout, stderr = await ssh_run(alias, remote_cmd)
    if code != 0:
        message = stderr.decode().strip() or "remote stat failed"
        raise OSError(message)
    try:
        return int(stdout.decode().strip())
    except ValueError as exc:
        raise OSError("remote stat returned invalid size") from exc


async def ssh_remote_codex_rollouts(
    alias: str,
    *,
    codex_home: str = "~/.codex",
) -> list[RemoteCodexRollout]:
    """List Codex rollout JSONL paths on a remote host, newest first."""
    home = _remote_codex_home(codex_home)
    remote_cmd = (
        f"find {home}/sessions -type f -name 'rollout-*.jsonl' -print0 2>/dev/null; "
        f"find {home}/archived_sessions -maxdepth 1 -type f -name 'rollout-*.jsonl' -print0 2>/dev/null; "
        "true"
    )
    code, stdout, stderr = await ssh_run(alias, remote_cmd, timeout_s=60.0)
    if code != 0:
        message = stderr.decode().strip() or "remote find failed"
        raise OSError(message)
    paths = [part.decode("utf-8") for part in stdout.split(b"\0") if part]
    rollouts: list[RemoteCodexRollout] = []
    for path in paths:
        stat_cmd = (
            f"stat -c %Y {shlex.quote(path)} 2>/dev/null "
            f"|| stat -f %m {shlex.quote(path)}"
        )
        stat_code, stat_out, _ = await ssh_run(alias, stat_cmd)
        if stat_code != 0:
            continue
        try:
            mtime_s = int(stat_out.decode().strip())
        except ValueError:
            continue
        rollouts.append(RemoteCodexRollout(path=path, mtime_ms=mtime_s * 1000))
    rollouts.sort(key=lambda entry: entry.mtime_ms, reverse=True)
    return rollouts


async def ssh_remote_active_codex_rollout(
    alias: str,
    thread_id: str,
    *,
    codex_home: str = "~/.codex",
) -> str | None:
    """Return the active remote rollout path for a Codex thread id, if present."""
    home = _remote_codex_home(codex_home)
    suffix = shlex.quote(f"*{thread_id}.jsonl")
    remote_cmd = f"find {home}/sessions -type f -name {suffix} 2>/dev/null | head -n 1"
    code, stdout, _ = await ssh_run(alias, remote_cmd)
    if code != 0:
        return None
    path = stdout.decode().strip()
    return path or None


async def ssh_remote_rollout_to_tempfile(
    alias: str,
    remote_path: str,
    *,
    byte_offset: int = 0,
) -> Path:
    """Download a remote rollout tail into a local temporary file."""
    payload = await ssh_remote_file_bytes(alias, remote_path, byte_offset=byte_offset)
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
    handle.write(payload)
    handle.flush()
    handle.close()
    return Path(handle.name)
