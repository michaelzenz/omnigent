"""Remote file operations over configured SSH connection profiles."""

from __future__ import annotations

import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path

from omnigent.ssh_connections_store import SshConnectionProfile
from omnigent.ssh_session import get_ssh_pool

_DEFAULT_TIMEOUT_S = 30.0
_DEFAULT_REMOTE_CODEX_HOME = "$HOME/.codex"


def _remote_codex_home(codex_home: str) -> str:
    """Return a remote-shell path for Codex home (tilde must not be quoted)."""
    if codex_home in {"~/.codex", "~"}:
        return _DEFAULT_REMOTE_CODEX_HOME
    if codex_home.startswith("~/"):
        return f"$HOME/{codex_home[2:]}"
    return codex_home


def _bash_lc(command: str) -> str:
    """Run *command* under bash for consistent pipe/glob behavior on zsh remotes."""
    return f"bash -lc {shlex.quote(command)}"


@dataclass(frozen=True)
class RemoteCodexRollout:
    """One Codex rollout path discovered on a remote host."""

    path: str
    mtime_ms: int
    size: int


async def ssh_run(
    profile: SshConnectionProfile,
    remote_command: str,
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> tuple[int, bytes, bytes]:
    """Run one remote shell command via the pooled session for *profile*."""
    return await get_ssh_pool().run(profile.alias, remote_command, timeout_s=timeout_s)


async def ssh_remote_path_exists(profile: SshConnectionProfile, remote_path: str) -> bool:
    """Return whether *remote_path* exists on the remote host."""
    quoted = shlex.quote(remote_path)
    code, _, _ = await ssh_run(profile, f"test -f {quoted}")
    return code == 0


async def ssh_remote_file_bytes(
    profile: SshConnectionProfile,
    remote_path: str,
    *,
    byte_offset: int = 0,
) -> bytes:
    """Read a remote file from *byte_offset* onward."""
    quoted = shlex.quote(remote_path)
    if byte_offset <= 0:
        remote_cmd = f"cat {quoted}"
    else:
        remote_cmd = f"tail -c +{byte_offset + 1} {quoted}"
    code, stdout, stderr = await ssh_run(profile, remote_cmd)
    if code != 0:
        message = stderr.decode().strip() or stdout.decode().strip() or "remote read failed"
        raise OSError(message)
    return stdout


async def ssh_remote_file_size(profile: SshConnectionProfile, remote_path: str) -> int:
    """Return the byte size of a remote file."""
    quoted = shlex.quote(remote_path)
    remote_cmd = f"wc -c < {quoted}"
    code, stdout, stderr = await ssh_run(profile, remote_cmd)
    if code != 0:
        message = stderr.decode().strip() or "remote stat failed"
        raise OSError(message)
    try:
        return int(stdout.decode().strip())
    except ValueError as exc:
        raise OSError("remote stat returned invalid size") from exc


def _parse_rollout_listing(stdout: bytes) -> list[RemoteCodexRollout]:
    """Parse null-delimited path/mtime/size triplets from a remote rollout listing."""
    parts = [part.decode("utf-8") for part in stdout.split(b"\0") if part]
    rollouts: list[RemoteCodexRollout] = []
    index = 0
    while index + 2 < len(parts):
        path = parts[index]
        try:
            mtime_s = int(parts[index + 1])
            size = int(parts[index + 2])
        except ValueError:
            index += 3
            continue
        rollouts.append(RemoteCodexRollout(path=path, mtime_ms=mtime_s * 1000, size=size))
        index += 3
    rollouts.sort(key=lambda entry: entry.mtime_ms, reverse=True)
    return rollouts


async def ssh_remote_codex_rollouts(
    profile: SshConnectionProfile,
    *,
    codex_home: str = "~/.codex",
) -> list[RemoteCodexRollout]:
    """List Codex rollout JSONL paths on a remote host, newest first."""
    home = _remote_codex_home(codex_home)
    remote_cmd = _bash_lc(
        f"(find {home}/sessions -type f -name 'rollout-*.jsonl' -print0 2>/dev/null; "
        f"find {home}/archived_sessions -maxdepth 1 -type f -name 'rollout-*.jsonl' -print0 2>/dev/null) | "
        "while IFS= read -r -d '' path; do "
        'mtime=$(stat -c %Y "$path" 2>/dev/null || stat -f %m "$path" 2>/dev/null) || continue; '
        'size=$(wc -c < "$path" 2>/dev/null) || continue; '
        "printf '%s\\0%s\\0%s\\0' \"$path\" \"$mtime\" \"$size\"; "
        "done"
    )
    code, stdout, stderr = await ssh_run(profile, remote_cmd, timeout_s=60.0)
    if code != 0:
        message = stderr.decode().strip() or "remote find failed"
        raise OSError(message)
    return _parse_rollout_listing(stdout)


async def ssh_remote_active_codex_rollout(
    profile: SshConnectionProfile,
    thread_id: str,
    *,
    codex_home: str = "~/.codex",
) -> str | None:
    """Return the active remote rollout path for a Codex thread id, if present."""
    home = _remote_codex_home(codex_home)
    suffix = shlex.quote(f"*{thread_id}.jsonl")
    remote_cmd = _bash_lc(
        f"find {home}/sessions -type f -name {suffix} 2>/dev/null | head -n 1"
    )
    code, stdout, _ = await ssh_run(profile, remote_cmd)
    if code != 0:
        return None
    path = stdout.decode().strip()
    return path or None


async def ssh_remote_missing_rollout_thread_ids(
    profile: SshConnectionProfile,
    entries: list[tuple[str, str]],
    *,
    codex_home: str = "~/.codex",
) -> set[str]:
    """Return thread ids with no active or archived rollout left on the remote host."""
    if not entries:
        return set()
    home = _remote_codex_home(codex_home)
    checks: list[str] = []
    for thread_id, rollout_path in entries:
        quoted_thread = shlex.quote(thread_id)
        quoted_path = shlex.quote(rollout_path)
        checks.append(
            f'active=$(find {home}/sessions -type f -name "*{thread_id}.jsonl" 2>/dev/null | head -n 1); '
            f'if [ -z "$active" ] && [ ! -f {quoted_path} ]; then printf "%s\\0" {quoted_thread}; fi'
        )
    remote_cmd = _bash_lc("; ".join(checks))
    code, stdout, stderr = await ssh_run(profile, remote_cmd, timeout_s=60.0)
    if code != 0:
        message = stderr.decode().strip() or "remote prune check failed"
        raise OSError(message)
    return {part.decode("utf-8") for part in stdout.split(b"\0") if part}


async def ssh_remote_rollout_to_tempfile(
    profile: SshConnectionProfile,
    remote_path: str,
    *,
    byte_offset: int = 0,
) -> Path:
    """Download a remote rollout tail into a local temporary file."""
    payload = await ssh_remote_file_bytes(profile, remote_path, byte_offset=byte_offset)
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
    handle.write(payload)
    handle.flush()
    handle.close()
    return Path(handle.name)
