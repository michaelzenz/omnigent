"""Tests for SSH connection profile storage."""

from __future__ import annotations

from pathlib import Path

from omnigent.ssh_connections_store import (
    SshConnectionProfile,
    read_ssh_connections,
    write_ssh_connections,
)


def test_write_and_read_ssh_connections(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    profile = SshConnectionProfile(
        id="abc123",
        label="Arca",
        alias="arca.ssh",
        created_at="2026-01-01T00:00:00+00:00",
    )
    write_ssh_connections([profile], config_path=config_path)
    loaded = read_ssh_connections(config_path=config_path)
    assert loaded == [profile]
