"""Tests for the GlobalHub manager store."""

from __future__ import annotations

import itertools
import uuid
from typing import Any

import pytest

from omnigent.stores.manager_store.sqlalchemy_store import SqlAlchemyManagerStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore

ALICE = "alice@example.com"
BOB = "bob@example.com"
HOST_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
# Creation order ties on the one-second created_at, then falls back to id.
_ids = (f"{n:032x}" for n in itertools.count(1))


@pytest.fixture()
def store(db_uri: str) -> SqlAlchemyManagerStore:
    return SqlAlchemyManagerStore(db_uri)


def _create(
    store: SqlAlchemyManagerStore, title: str, *, owner: str | None = ALICE, **fields: Any
) -> str:
    manager_id = next(_ids)
    store.create(manager_id, owner_user_id=owner, title=title, **fields)
    return manager_id


def test_managers_are_listed_per_owner_in_creation_order(store: SqlAlchemyManagerStore) -> None:
    storage = _create(store, "Storage")
    _create(store, "Dashboards")
    _create(store, "Bob's", owner=BOB)
    _create(store, "Local", owner=None)

    assert [m.title for m in store.list(owner_user_id=ALICE)] == ["Storage", "Dashboards"]
    assert [m.title for m in store.list(owner_user_id=None)] == ["Local"]
    manager = store.get(storage, owner_user_id=ALICE)
    assert manager is not None
    assert (manager.description, manager.conversation_id) == ("", None)
    assert store.get(storage, owner_user_id=BOB) is None


def test_get_by_conversation_id_is_owner_scoped(store: SqlAlchemyManagerStore) -> None:
    session_id = uuid.uuid4().hex
    _create(store, "Storage", conversation_id=session_id)

    manager = store.get_by_conversation_id(session_id, owner_user_id=ALICE)
    assert manager is not None and manager.title == "Storage"
    assert store.get_by_conversation_id(session_id, owner_user_id=BOB) is None


def test_update_changes_only_the_given_fields(store: SqlAlchemyManagerStore) -> None:
    session_id = uuid.uuid4().hex
    manager_id = _create(
        store,
        "Storage",
        conversation_id=session_id,
        host_id=HOST_ID,
        workspace="/Users/alice/storage",
    )

    described = store.update(manager_id, owner_user_id=ALICE, description="Storage services")
    detached = store.update(
        manager_id, owner_user_id=ALICE, conversation_id=None, host_id=None, workspace=None
    )

    assert described is not None
    assert (described.description, described.conversation_id, described.host_id) == (
        "Storage services",
        session_id,
        HOST_ID,
    )
    assert detached is not None
    assert (
        detached.description,
        detached.conversation_id,
        detached.host_id,
        detached.workspace,
    ) == ("Storage services", None, None, None)
    assert store.update(manager_id, owner_user_id=BOB, title="Taken") is None


def test_delete_is_owner_scoped(store: SqlAlchemyManagerStore) -> None:
    manager_id = _create(store, "Storage")

    assert store.delete(manager_id, owner_user_id=BOB) is False
    assert store.delete(manager_id, owner_user_id=ALICE) is True
    assert store.list(owner_user_id=ALICE) == []


def test_deleting_a_manager_unassigns_its_tasks_and_returns_its_events(
    db_uri: str, store: SqlAlchemyManagerStore
) -> None:
    tasks = SqlAlchemyTaskStore(db_uri)
    events = SqlAlchemyTaskEventStore(db_uri)
    manager_id = _create(store, "Storage")
    task_id = uuid.uuid4().hex
    tasks.create(task_id, owner_user_id=ALICE, title="Task", manager_id=manager_id)
    unhandled, handled = uuid.uuid4().hex, uuid.uuid4().hex
    for event_id in (unhandled, handled):
        events.create(
            event_id,
            owner_user_id=ALICE,
            event_type="github.pr.comment",
            title="Review comment",
            manager_id=manager_id,
        )
    events.dismiss(handled, owner_user_id=ALICE)

    assert store.delete(manager_id, owner_user_id=ALICE) is True

    task = tasks.get(task_id, owner_user_id=ALICE)
    assert task is not None and task.manager_id is None
    returned = events.get(unhandled, owner_user_id=ALICE)
    kept = events.get(handled, owner_user_id=ALICE)
    assert returned is not None and kept is not None
    assert (returned.state, returned.manager_id, returned.routed_at) == ("unrouted", None, None)
    assert (kept.state, kept.manager_id) == ("dismissed", manager_id)
