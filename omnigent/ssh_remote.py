"""Remote command and file primitives over configured SSH connection profiles.

The transport layer for provisioning a remote machine: running commands to
install and launch ``omnigent host``, and reading files back to verify the
result. Connection reuse lives in :mod:`omnigent.ssh_session`.
"""

from __future__ import annotations

import shlex
import tempfile
from pathlib import Path

from omnigent.entities import SshConnectionProfile
from omnigent.ssh_session import get_ssh_pool

_DEFAULT_TIMEOUT_S = 30.0


def bash_lc(command: str) -> str:
    """Run *command* under bash for consistent pipe/glob behavior on zsh remotes."""
    return f"bash -lc {shlex.quote(command)}"


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


async def ssh_remote_file_to_tempfile(
    profile: SshConnectionProfile,
    remote_path: str,
) -> Path:
    """Download a remote file into a local temporary file."""
    payload = await ssh_remote_file_bytes(profile, remote_path)
    handle = tempfile.NamedTemporaryFile(delete=False)
    handle.write(payload)
    handle.flush()
    handle.close()
    return Path(handle.name)
