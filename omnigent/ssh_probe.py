"""SSH connectivity probe used by the settings UI.

Runs ``ssh`` in batch mode from the machine hosting the Omnigent server
process (where the user's ``~/.ssh`` config and keys typically live for
local deploys). Uses a subprocess with argument lists — never a shell.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from omnigent.entities.ssh_connection import validate_ssh_alias

_PROBE_ECHO = "omnigent-ssh-ok"
_DEFAULT_TIMEOUT_S = 15.0


@dataclass(frozen=True)
class SshProbeRequest:
    """Validated SSH config Host alias for a connectivity probe."""

    alias: str


@dataclass(frozen=True)
class SshProbeResult:
    """Outcome of an SSH connectivity probe."""

    ok: bool
    message: str
    latency_ms: int | None = None


def build_ssh_probe_command(alias: str) -> list[str]:
    """Build the ``ssh`` argv for a non-interactive alias probe."""
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={int(_DEFAULT_TIMEOUT_S)}",
        "-o",
        "LogLevel=ERROR",
        alias.strip(),
        f"echo {_PROBE_ECHO}",
    ]


async def probe_ssh(req: SshProbeRequest) -> SshProbeResult:
    """Run an SSH echo probe against a config Host alias."""
    error = validate_ssh_alias(req.alias)
    if error is not None:
        return SshProbeResult(ok=False, message=error)

    alias = req.alias.strip()
    cmd = build_ssh_probe_command(alias)
    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return SshProbeResult(ok=False, message="ssh command not found on this machine")

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=_DEFAULT_TIMEOUT_S + 3.0,
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return SshProbeResult(ok=False, message="SSH probe timed out")

    latency_ms = int((time.monotonic() - started) * 1000)
    if proc.returncode == 0 and stdout.decode().strip() == _PROBE_ECHO:
        return SshProbeResult(ok=True, message="Connected", latency_ms=latency_ms)

    err_text = stderr.decode().strip() or stdout.decode().strip() or "SSH connection failed"
    first_line = err_text.splitlines()[0] if err_text else "SSH connection failed"
    return SshProbeResult(ok=False, message=first_line, latency_ms=latency_ms)
