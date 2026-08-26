"""Tests for database-backed SSH connection profile storage."""

from __future__ import annotations

from pathlib import Path

from omnigent.db.db_models import OmnigentBase
from omnigent.db.utils import get_or_create_engine
from omnigent.entities import SshConnectionProfile
from omnigent.entities.ssh_connection import validate_package_index_url
from omnigent.stores.ssh_host_installation_store import SshHostInstallationStore


def _store(tmp_path: Path) -> SshHostInstallationStore:
    uri = f"sqlite:///{tmp_path / 'ssh-settings.db'}"
    OmnigentBase.metadata.create_all(get_or_create_engine(uri))
    return SshHostInstallationStore(uri)


def test_write_and_read_ssh_connections(tmp_path: Path) -> None:
    store = _store(tmp_path)
    profile = SshConnectionProfile(
        id="abc123",
        label="Arca",
        alias="arca.ssh",
        created_at="2026-01-01T00:00:00+00:00",
        owner="admin@example.com",
    )
    store.sync_connections(
        {profile.id: profile},
        bundle_version="test",
        owner="local",
    )
    loaded = store.profiles()
    assert loaded == [profile]


def test_write_and_read_ssh_settings(tmp_path: Path) -> None:
    store = _store(tmp_path)
    initial = store.get_settings()
    store.update_settings(
        package_index_url="https://pypi.example.com/simple",
        npm_registry_url=None,
        updated_by="admin@example.com",
    )
    updated = store.get_settings()
    assert updated.package_index_url == "https://pypi.example.com/simple"
    assert updated.remote_namespace == initial.remote_namespace
    assert len(updated.remote_namespace) == 12


def test_validate_package_index_url_rejects_non_https() -> None:
    assert validate_package_index_url("http://pypi.example.com/simple") is not None
    assert validate_package_index_url("https://pypi.example.com/simple") is None
