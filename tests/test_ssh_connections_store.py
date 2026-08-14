"""Tests for SSH connection profile storage."""

from __future__ import annotations

from pathlib import Path

from omnigent.ssh_connections_store import (
    SshConnectionProfile,
    SshSettings,
    read_ssh_connections,
    read_ssh_settings,
    validate_package_index_url,
    write_ssh_connections,
    write_ssh_settings,
)


def test_write_and_read_ssh_connections(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    profile = SshConnectionProfile(
        id="abc123",
        label="Arca",
        alias="arca.ssh",
        created_at="2026-01-01T00:00:00+00:00",
        owner="admin@example.com",
    )
    write_ssh_connections([profile], config_path=config_path)
    loaded = read_ssh_connections(config_path=config_path)
    assert loaded == [profile]


def test_write_and_read_ssh_settings(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    write_ssh_settings(
        SshSettings(package_index_url="https://pypi.example.com/simple"),
        config_path=config_path,
    )
    assert read_ssh_settings(config_path=config_path) == SshSettings(
        package_index_url="https://pypi.example.com/simple",
    )


def test_validate_package_index_url_rejects_non_https() -> None:
    assert validate_package_index_url("http://pypi.example.com/simple") is not None
    assert validate_package_index_url("https://pypi.example.com/simple") is None
