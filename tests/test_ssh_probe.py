"""Tests for SSH connectivity probing."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from omnigent.ssh_probe import (
    SshProbeRequest,
    build_ssh_probe_command,
    probe_ssh,
    validate_ssh_alias,
)


def test_validate_ssh_alias_rejects_invalid_values() -> None:
    assert validate_ssh_alias("") is not None
    assert validate_ssh_alias("bad alias") is not None


def test_validate_ssh_alias_accepts_config_host() -> None:
    assert validate_ssh_alias("arca.ssh") is None


def test_build_ssh_probe_command_uses_alias() -> None:
    cmd = build_ssh_probe_command("arca.ssh")
    assert cmd[0] == "ssh"
    assert "arca.ssh" in cmd
    assert cmd[-1] == "echo omnigent-ssh-ok"


@pytest.mark.asyncio
async def test_probe_ssh_success() -> None:
    class _Proc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return (b"omnigent-ssh-ok\n", b"")

    with patch("omnigent.ssh_probe.asyncio.create_subprocess_exec", return_value=_Proc()):
        result = await probe_ssh(SshProbeRequest(alias="arca.ssh"))
    assert result.ok is True
    assert result.message == "Connected"
    assert result.latency_ms is not None


@pytest.mark.asyncio
async def test_probe_ssh_failure_returns_stderr_line() -> None:
    class _Proc:
        returncode = 255

        async def communicate(self) -> tuple[bytes, bytes]:
            return (b"", b"Permission denied (publickey).\n")

    with patch("omnigent.ssh_probe.asyncio.create_subprocess_exec", return_value=_Proc()):
        result = await probe_ssh(SshProbeRequest(alias="arca.ssh"))
    assert result.ok is False
    assert result.message == "Permission denied (publickey)."
