"""Tests for :class:`SqlAlchemyTaskAssetStore`."""

from __future__ import annotations

import uuid

import pytest

from omnigent.stores.task_asset_store.sqlalchemy_store import SqlAlchemyTaskAssetStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.fixture()
def store(db_uri: str) -> SqlAlchemyTaskAssetStore:
    return SqlAlchemyTaskAssetStore(db_uri)


def test_create_and_list_assets(store: SqlAlchemyTaskAssetStore) -> None:
    task_id = _uid("task_a")
    first = store.create_asset(task_id, kind="url", title="PR #1", url="https://example.com/1")
    second = store.create_asset(task_id, kind="url", title="PR #2", url="https://example.com/2")

    assets = store.list_assets_for_task(task_id)
    assert [asset.id for asset in assets] == [first.id, second.id]
    assert assets[0].title == "PR #1"
    assert assets[0].url == "https://example.com/1"


def test_list_is_scoped_to_one_task(store: SqlAlchemyTaskAssetStore) -> None:
    task_a = _uid("task_a")
    task_b = _uid("task_b")
    store.create_asset(task_a, kind="url", title="A", url="https://example.com/a")
    store.create_asset(task_b, kind="url", title="B", url="https://example.com/b")

    assert [asset.title for asset in store.list_assets_for_task(task_a)] == ["A"]
    assert [asset.title for asset in store.list_assets_for_task(task_b)] == ["B"]


def test_delete_asset(store: SqlAlchemyTaskAssetStore) -> None:
    task_id = _uid("task_del")
    created = store.create_asset(task_id, kind="url", title="PR #1", url="https://example.com/1")

    assert store.delete_asset(task_id, created.id) is True
    assert store.list_assets_for_task(task_id) == []

    # A second delete of the same id is a no-op (returns False).
    assert store.delete_asset(task_id, created.id) is False


def test_delete_asset_is_scoped_to_task(store: SqlAlchemyTaskAssetStore) -> None:
    task_a = _uid("task_scoped_a")
    task_b = _uid("task_scoped_b")
    asset_a = store.create_asset(task_a, kind="url", title="A", url="https://example.com/a")

    # An asset id belonging to task_a cannot be deleted through task_b.
    assert store.delete_asset(task_b, asset_a.id) is False
    assert [asset.title for asset in store.list_assets_for_task(task_a)] == ["A"]


def test_upsert_asset_provenance_lifecycle(store: SqlAlchemyTaskAssetStore) -> None:
    """Provenance stamps on harvest, re-points on re-harvest, survives a
    provenance-less manual re-post, and stays absent for human adds."""
    task_id = _uid("task_prov")

    harvested = store.upsert_asset(
        task_id,
        kind="workspace",
        category="workspace",
        title="feat/x",
        url="/repo",
        source_worker_id="w_1",
    )
    assert harvested.source_worker_id == "w_1"

    # Re-harvest by another worker re-points the chip (same row, url identity).
    reharvested = store.upsert_asset(
        task_id,
        kind="workspace",
        category="workspace",
        title="feat/y",
        url="/repo",
        source_worker_id="w_2",
    )
    assert reharvested.id == harvested.id
    assert reharvested.source_worker_id == "w_2"

    # Manual re-post without provenance preserves the existing chip.
    manual = store.upsert_asset(
        task_id, kind="workspace", category="workspace", title="feat/z", url="/repo"
    )
    assert manual.source_worker_id == "w_2"

    # Human-added asset has no provenance.
    human = store.create_asset(task_id, kind="url", title="doc", url="https://example.com")
    assert human.source_worker_id is None

    assets = store.list_assets_for_task(task_id)
    assert [asset.source_worker_id for asset in assets] == ["w_2", None]
