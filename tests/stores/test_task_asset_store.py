"""Tests for the GlobalHub task asset store."""

from __future__ import annotations

import itertools
import uuid
from typing import Any

import pytest

from omnigent.entities import TaskAsset
from omnigent.stores.task_asset_store.sqlalchemy_store import SqlAlchemyTaskAssetStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore

ALICE = "alice@example.com"
BOB = "bob@example.com"
TASK = f"{10:032x}"
OTHER_TASK = f"{11:032x}"
DOC_URL = "https://example.com/design-doc"
# Attach order ties on the one-second created_at, then falls back to id.
_ids = (f"{n:032x}" for n in itertools.count(1))


@pytest.fixture()
def store(db_uri: str) -> SqlAlchemyTaskAssetStore:
    return SqlAlchemyTaskAssetStore(db_uri)


def _attach(
    store: SqlAlchemyTaskAssetStore,
    *,
    url: str = DOC_URL,
    title: str = "Design doc",
    task_id: str = TASK,
    owner: str = ALICE,
    **fields: Any,
) -> TaskAsset:
    return store.upsert(
        next(_ids), owner_user_id=owner, task_id=task_id, url=url, title=title, **fields
    )


def test_a_task_has_one_asset_per_url_and_a_repeat_updates_it(
    store: SqlAlchemyTaskAssetStore,
) -> None:
    first_session, second_session = f"{200:032x}", f"{201:032x}"

    first = _attach(
        store, title="Draft", category="documents", add_related_session_ids=[first_session]
    )
    updated = _attach(
        store, category="runbooks", add_related_session_ids=[second_session, first_session]
    )
    repeated = _attach(store, category="runbooks")
    on_other_task = _attach(store, task_id=OTHER_TASK)

    assert first.id == updated.id == repeated.id
    assert (updated.title, updated.category, updated.related_session_ids) == (
        "Design doc",
        "runbooks",
        [first_session, second_session],
    )
    assert (repeated.related_session_ids, repeated.updated_at) == (
        [first_session, second_session],
        updated.updated_at,
    )
    assert first.updated_at is None and updated.updated_at is not None
    assert (on_other_task.id != first.id, on_other_task.category) == (True, "other")


def test_rosters_and_deletes_are_owner_scoped(store: SqlAlchemyTaskAssetStore) -> None:
    doc = _attach(store)
    pull_request = _attach(store, url="https://example.com/pull/1", title="Fix", category="code")
    other_task = _attach(store, task_id=OTHER_TASK)
    _attach(store, owner=BOB)

    def roster(*task_ids: str) -> list[str]:
        return [asset.id for asset in store.list_for_tasks(task_ids, owner_user_id=ALICE)]

    assert roster(TASK, OTHER_TASK) == [doc.id, pull_request.id, other_task.id]
    assert store.delete(doc.id, owner_user_id=BOB, task_id=TASK) is False
    assert store.delete(doc.id, owner_user_id=ALICE, task_id=OTHER_TASK) is False
    assert store.delete(doc.id, owner_user_id=ALICE, task_id=TASK) is True
    assert roster(TASK) == [pull_request.id]
    assert store.list_for_tasks([], owner_user_id=ALICE) == []


def test_deleting_a_task_deletes_its_assets(db_uri: str, store: SqlAlchemyTaskAssetStore) -> None:
    tasks = SqlAlchemyTaskStore(db_uri)
    task_id = uuid.uuid4().hex
    tasks.create(task_id, owner_user_id=ALICE, title="Task", goal="Finish the task")
    _attach(store, task_id=task_id)
    kept = _attach(store)

    assert tasks.delete(task_id, owner_user_id=ALICE) is True

    remaining = store.list_for_tasks([task_id, TASK], owner_user_id=ALICE)
    assert [asset.id for asset in remaining] == [kept.id]
