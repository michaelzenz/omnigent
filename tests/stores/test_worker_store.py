"""Tests for the GlobalHub worker store."""

from __future__ import annotations

import itertools
import uuid
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore

ALICE = "alice@example.com"
BOB = "bob@example.com"
TASK = f"{10:032x}"
OTHER_TASK = f"{11:032x}"
# Attach order ties on the one-second created_at, then falls back to id.
_ids = (f"{n:032x}" for n in itertools.count(1))


@pytest.fixture()
def store(db_uri: str) -> SqlAlchemyWorkerStore:
    return SqlAlchemyWorkerStore(db_uri)


def _attach(
    store: SqlAlchemyWorkerStore,
    *,
    task_id: str = TASK,
    session_id: str | None = None,
    owner: str | None = ALICE,
    **fields: Any,
) -> str:
    worker_id = next(_ids)
    store.create(
        worker_id,
        owner_user_id=owner,
        task_id=task_id,
        session_id=session_id or uuid.uuid4().hex,
        **fields,
    )
    return worker_id


def test_workers_wrap_sessions_for_their_owners_tasks(store: SqlAlchemyWorkerStore) -> None:
    session_id = uuid.uuid4().hex
    worker_id = _attach(store, session_id=session_id, title="Fixing retries")

    worker = store.get(worker_id, owner_user_id=ALICE)
    assert worker is not None
    assert (worker.task_id, worker.session_id, worker.title) == (
        TASK,
        session_id,
        "Fixing retries",
    )
    assert store.get(worker_id, owner_user_id=BOB) is None
    found = store.get_for_task_and_session(TASK, session_id, owner_user_id=ALICE)
    assert found is not None and found.id == worker_id
    assert store.get_for_task_and_session(TASK, session_id, owner_user_id=BOB) is None


def test_a_session_has_one_worker_per_task(store: SqlAlchemyWorkerStore) -> None:
    session_id = uuid.uuid4().hex
    _attach(store, session_id=session_id)
    _attach(store, task_id=OTHER_TASK, session_id=session_id)

    with pytest.raises(IntegrityError):
        _attach(store, session_id=session_id)


def test_rosters_list_by_task_in_attach_order(store: SqlAlchemyWorkerStore) -> None:
    first = _attach(store)
    second = _attach(store)
    other = _attach(store, task_id=OTHER_TASK)
    _attach(store, owner=BOB)

    workers = store.list_for_tasks([TASK, OTHER_TASK], owner_user_id=ALICE)

    assert [worker.id for worker in workers] == [first, second, other]
    assert store.list_for_tasks([], owner_user_id=ALICE) == []


def test_a_sessions_workers_list_in_attach_order(store: SqlAlchemyWorkerStore) -> None:
    session_id = uuid.uuid4().hex
    first = _attach(store, session_id=session_id)
    second = _attach(store, task_id=OTHER_TASK, session_id=session_id)
    _attach(store)

    workers = store.list_for_session(session_id, owner_user_id=ALICE)

    assert [worker.id for worker in workers] == [first, second]
    assert store.list_for_session(session_id, owner_user_id=BOB) == []


def test_update_retitles_only_as_asked(store: SqlAlchemyWorkerStore) -> None:
    worker_id = _attach(store, title="Fixing retries")

    unchanged = store.update(worker_id, owner_user_id=ALICE)
    retitled = store.update(worker_id, owner_user_id=ALICE, title=None)

    assert unchanged is not None
    assert (unchanged.title, unchanged.updated_at) == ("Fixing retries", None)
    assert retitled is not None
    assert (retitled.title, retitled.updated_at is not None) == (None, True)
    assert store.update(worker_id, owner_user_id=BOB, title="Taken") is None


def test_untracking_deletes_only_the_owners_worker(store: SqlAlchemyWorkerStore) -> None:
    worker_id = _attach(store)

    assert store.delete(worker_id, owner_user_id=BOB) is False
    assert store.delete(worker_id, owner_user_id=ALICE) is True
    assert store.get(worker_id, owner_user_id=ALICE) is None


def test_deleting_a_task_deletes_its_workers(db_uri: str, store: SqlAlchemyWorkerStore) -> None:
    tasks = SqlAlchemyTaskStore(db_uri)
    task_id = uuid.uuid4().hex
    tasks.create(task_id, owner_user_id=ALICE, title="Task", goal="Finish the task")
    worker_id = _attach(store, task_id=task_id)
    other_task_worker = _attach(store)

    assert tasks.delete(task_id, owner_user_id=ALICE) is True

    assert store.get(worker_id, owner_user_id=ALICE) is None
    assert store.get(other_task_worker, owner_user_id=ALICE) is not None
