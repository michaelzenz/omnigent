"""Tests for database-backed, per-user SSH connection profile storage."""

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


def _profile(profile_id: str = "abc123", alias: str = "arca.ssh") -> SshConnectionProfile:
    return SshConnectionProfile(
        id=profile_id,
        label="Arca",
        alias=alias,
        created_at="2026-01-01T00:00:00+00:00",
        owner="admin@example.com",
    )


def test_write_and_read_ssh_connections(tmp_path: Path) -> None:
    store = _store(tmp_path)
    profile = _profile()
    store.sync_connections(
        {profile.id: profile},
        bundle_version="test",
        owner="admin@example.com",
    )
    loaded = store.profiles("admin@example.com")
    assert loaded == [profile]


def test_connections_are_scoped_per_user(tmp_path: Path) -> None:
    """Each user manages their own set; syncing one never touches the other's."""
    store = _store(tmp_path)
    mine = _profile("mine-1", "my-box")
    theirs = _profile("theirs-1", "their-box")
    store.sync_connections({mine.id: mine}, bundle_version="test", owner="alice@example.com")
    store.sync_connections({theirs.id: theirs}, bundle_version="test", owner="bob@example.com")

    # Bob's save drops Bob's connection only; Alice's row survives.
    store.sync_connections({}, bundle_version="test", owner="bob@example.com")

    assert [p.id for p in store.profiles("alice@example.com")] == [mine.id]
    assert store.profiles("bob@example.com") == []
    row = store.snapshots("alice@example.com").get(mine.id)
    assert row is not None
    assert row.desired_state == "connected"


def test_alias_uniqueness_is_per_user(tmp_path: Path) -> None:
    """Two users may each configure the same SSH alias."""
    store = _store(tmp_path)
    store.sync_connections(
        {_profile("a-1").id: _profile("a-1")}, bundle_version="test", owner="alice@example.com"
    )
    store.sync_connections(
        {_profile("b-1").id: _profile("b-1")}, bundle_version="test", owner="bob@example.com"
    )
    assert len(store.profiles("alice@example.com")) == 1
    assert len(store.profiles("bob@example.com")) == 1


def test_write_and_read_ssh_settings(tmp_path: Path) -> None:
    store = _store(tmp_path)
    initial = store.get_settings("admin@example.com")
    store.update_settings(
        owner="admin@example.com",
        package_index_url="https://pypi.example.com/simple",
        npm_registry_url=None,
    )
    updated = store.get_settings("admin@example.com")
    assert updated.package_index_url == "https://pypi.example.com/simple"
    assert updated.remote_namespace == initial.remote_namespace
    assert len(updated.remote_namespace) == 12


def test_settings_are_scoped_per_user(tmp_path: Path) -> None:
    """Each user configures their own registries and keeps their own namespace."""
    store = _store(tmp_path)
    alice = store.get_settings("alice@example.com")
    bob = store.get_settings("bob@example.com")
    assert alice.remote_namespace != bob.remote_namespace

    store.update_settings(
        owner="alice@example.com",
        package_index_url="https://pypi.example.com/simple",
        npm_registry_url=None,
    )
    assert store.get_settings("bob@example.com").package_index_url is None
    assert store.get_settings("alice@example.com").package_index_url == (
        "https://pypi.example.com/simple"
    )


def test_retry_now_is_owner_scoped(tmp_path: Path) -> None:
    store = _store(tmp_path)
    profile = _profile()
    store.sync_connections({profile.id: profile}, bundle_version="test", owner="alice@example.com")

    assert not store.retry_now(profile.id, owner="bob@example.com")
    assert store.retry_now(profile.id, owner="alice@example.com")


def test_pause_unfinished_marks_only_in_flight_rows(tmp_path: Path) -> None:
    """Ready rows keep their state; in-flight rows show the offline error."""
    store = _store(tmp_path)
    in_flight = _profile("in-flight")
    done = _profile("done", "done-box")
    store.sync_connections(
        {in_flight.id: in_flight, done.id: done},
        bundle_version="test",
        owner="alice@example.com",
    )
    leased = store.acquire("done", lease_owner="host-1", lease_seconds=30)
    assert leased is not None
    assert store.set_phase(
        "done", lease_owner="host-1", generation=leased.generation, phase="ready"
    )

    store.pause_unfinished_for_owner("alice@example.com", "daemon offline")

    snapshots = store.snapshots("alice@example.com")
    assert snapshots["in-flight"].phase == "paused_offline"
    assert snapshots["in-flight"].last_error == "daemon offline"
    assert snapshots["done"].phase == "ready"


def test_validate_package_index_url_rejects_non_https() -> None:
    assert validate_package_index_url("http://pypi.example.com/simple") is not None
    assert validate_package_index_url("https://pypi.example.com/simple") is None
