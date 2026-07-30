"""Tests for :class:`SqlAlchemyTimerItemStore`."""

from __future__ import annotations

import uuid

import pytest

from omnigent.db.utils import now_epoch
from omnigent.stores.timer_item_store.sqlalchemy_store import SqlAlchemyTimerItemStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.fixture()
def store(db_uri: str) -> SqlAlchemyTimerItemStore:
    return SqlAlchemyTimerItemStore(db_uri)


def test_create_and_get_item(store: SqlAlchemyTimerItemStore) -> None:
    item = store.create_item(
        _uid("timer_1"),
        "prompt",
        now_epoch() + 60,
        "host-a",
        {"session_id": "conv_1", "message": "hello"},
        owner_user_id="alice@example.com",
    )
    assert item.state == "pending"
    assert item.host_id == "host-a"
    assert item.payload == {"session_id": "conv_1", "message": "hello"}

    loaded = store.get_item(item.id)
    assert loaded is not None
    assert loaded.id == item.id
    assert loaded.task_type == "prompt"


def test_list_due_filters_by_host_and_time(store: SqlAlchemyTimerItemStore) -> None:
    now = now_epoch()
    due = store.create_item(_uid("due"), "prompt", now - 5, "host-a", {})
    store.create_item(_uid("future"), "prompt", now + 300, "host-a", {})
    store.create_item(_uid("other_host"), "prompt", now - 5, "host-b", {})

    items = store.list_due("host-a", now=now)
    assert [row.id for row in items] == [due.id]


def test_claim_complete_and_fail_lifecycle(store: SqlAlchemyTimerItemStore) -> None:
    now = now_epoch()
    item = store.create_item(_uid("claim"), "prompt", now - 1, "host-a", {})

    claimed = store.claim_item(item.id, "host-a")
    assert claimed is not None
    assert claimed.state == "running"
    assert claimed.fired_at is not None

    assert store.claim_item(item.id, "host-a") is None

    completed = store.complete_item(item.id, "host-a")
    assert completed is not None
    assert completed.state == "done"

    failed_item = store.create_item(_uid("fail"), "prompt", now - 1, "host-a", {})
    store.claim_item(failed_item.id, "host-a")
    failed = store.fail_item(failed_item.id, "host-a")
    assert failed is not None
    assert failed.state == "failed"


def test_host_mismatch_rejects_claim(store: SqlAlchemyTimerItemStore) -> None:
    now = now_epoch()
    item = store.create_item(_uid("mismatch"), "prompt", now - 1, "host-a", {})
    assert store.claim_item(item.id, "host-b") is None
    reloaded = store.get_item(item.id)
    assert reloaded is not None
    assert reloaded.state == "pending"
