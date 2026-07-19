"""Tests for multiplexed SSH session reuse."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from omnigent.ssh_session import (
    SshSession,
    SshSessionPool,
    build_ssh_close_command,
    build_ssh_command,
    control_path_for_alias,
    get_ssh_pool,
    reset_ssh_pool_for_tests,
    ssh_multiplex_options,
)


def test_control_path_for_alias_is_stable(tmp_path: Path) -> None:
    first = control_path_for_alias("arca.ssh", tmp_path)
    second = control_path_for_alias("arca.ssh", tmp_path)
    other = control_path_for_alias("other.ssh", tmp_path)
    assert first == second
    assert first != other
    assert first.parent == tmp_path


def test_ssh_multiplex_options_include_control_master(tmp_path: Path) -> None:
    control_path = tmp_path / "socket.sock"
    options = ssh_multiplex_options(control_path)
    assert "ControlMaster=auto" in options
    assert f"ControlPath={control_path}" in options
    assert any(option.startswith("ControlPersist=") for option in options)


def test_build_ssh_command_includes_remote_command(tmp_path: Path) -> None:
    control_path = tmp_path / "socket.sock"
    cmd = build_ssh_command("arca.ssh", "echo hi", control_path=control_path)
    assert cmd[0] == "ssh"
    assert "arca.ssh" in cmd
    assert cmd[-1] == "echo hi"


def test_build_ssh_close_command_uses_control_path(tmp_path: Path) -> None:
    control_path = tmp_path / "socket.sock"
    cmd = build_ssh_close_command("arca.ssh", control_path=control_path)
    assert cmd[:3] == ["ssh", "-O", "exit"]
    assert f"ControlPath={control_path}" in cmd


@pytest.mark.asyncio
async def test_ssh_session_run_uses_multiplex_argv(tmp_path: Path) -> None:
    captured: list[list[str]] = []

    class _Proc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return (b"ok\n", b"")

    async def _capture_exec(*argv: str, **_kwargs: object) -> _Proc:
        captured.append(list(argv))
        return _Proc()

    with patch("omnigent.ssh_session.asyncio.create_subprocess_exec", side_effect=_capture_exec):
        session = SshSession("arca.ssh", control_dir=tmp_path)
        code, stdout, _stderr = await session.run("echo ok")

    assert code == 0
    assert stdout == b"ok\n"
    assert captured
    assert "ControlMaster=auto" in captured[0]
    assert captured[0][-2] == "arca.ssh"
    assert captured[0][-1] == "echo ok"


@pytest.mark.asyncio
async def test_get_ssh_pool_returns_singleton(tmp_path: Path) -> None:
    first = reset_ssh_pool_for_tests(control_dir=tmp_path)
    second = get_ssh_pool()
    assert first is second


@pytest.mark.asyncio
async def test_ssh_session_pool_reuses_session(tmp_path: Path) -> None:
    pool = SshSessionPool(control_dir=tmp_path)
    first = pool.session("arca.ssh")
    second = pool.session("arca.ssh")
    third = pool.session("other.ssh")
    assert first is second
    assert first is not third


@pytest.mark.asyncio
async def test_ssh_session_pool_close_tears_down_master(tmp_path: Path) -> None:
    captured: list[list[str]] = []

    class _Proc:
        returncode = 0

        async def wait(self) -> int:
            return 0

    async def _capture_exec(*argv: str, **_kwargs: object) -> _Proc:
        captured.append(list(argv))
        return _Proc()

    session = SshSession("arca.ssh", control_dir=tmp_path)
    session._control_path.touch()
    pool = SshSessionPool(control_dir=tmp_path)
    pool._sessions["arca.ssh"] = session

    with patch("omnigent.ssh_session.asyncio.create_subprocess_exec", side_effect=_capture_exec):
        await pool.close()

    assert captured
    assert captured[0][:3] == ["ssh", "-O", "exit"]
