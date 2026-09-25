"""Store tests for worker target/task lookups."""

from __future__ import annotations

import uuid

import pytest

from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"worker-store-test:{seed}").hex


@pytest.fixture()
def worker_store(db_uri: str) -> SqlAlchemyWorkerStore:
    return SqlAlchemyWorkerStore(db_uri)


def test_find_worker_by_target_task_prefers_newest_live(
    worker_store: SqlAlchemyWorkerStore,
) -> None:
    """Duplicates for one (task, session) resolve to the newest live row."""
    session = _uid("sess-1")
    task = _uid("task-1")
    worker_store.create_worker(
        _uid("w-old"), task, kind="internal", target_id=session, state="idle"
    )
    worker_store.create_worker(
        _uid("w-new"), task, kind="internal", target_id=session, state="busy"
    )

    found = worker_store.find_worker_by_target_task(task, session)
    assert found is not None
    assert found.id == _uid("w-new")


def test_find_worker_by_target_task_skips_other_tasks(
    worker_store: SqlAlchemyWorkerStore,
) -> None:
    """A session bound to two tasks resolves each task to its own lane."""
    session = _uid("sess-2")
    task_a, task_b = _uid("task-a2"), _uid("task-b2")
    worker_store.create_worker(
        _uid("w-a"), task_a, kind="internal", target_id=session, state="idle"
    )
    worker_store.create_worker(
        _uid("w-b"), task_b, kind="internal", target_id=session, state="idle"
    )

    assert worker_store.find_worker_by_target_task(task_a, session).id == _uid("w-a")
    assert worker_store.find_worker_by_target_task(task_b, session).id == _uid("w-b")


def test_find_worker_by_target_task_returns_newest_any_state(
    worker_store: SqlAlchemyWorkerStore,
) -> None:
    """The pair lookup returns the newest row regardless of state —
    adoption's update/revive target. Newest-wins with explicit timestamps
    is pinned by the migration test."""
    session = _uid("sess-3")
    task = _uid("task-3")
    worker_store.create_worker(
        _uid("w-term"), task, kind="internal", target_id=session, state="terminated"
    )
    worker_store.create_worker(
        _uid("w-dead"), task, kind="internal", target_id=session, state="deleted"
    )

    found = worker_store.find_worker_by_target_task(task, session)
    assert found is not None
    assert found.state in {"terminated", "deleted"}


def test_find_worker_by_target_task_unknown_pair(worker_store: SqlAlchemyWorkerStore) -> None:
    assert worker_store.find_worker_by_target_task(_uid("task-x"), _uid("sess-x")) is None


def test_get_by_target_id_prefers_live_rows(worker_store: SqlAlchemyWorkerStore) -> None:
    """Legacy duplicate rows: the oldest live row wins, dead ones are skipped."""
    session = _uid("sess-4")
    worker_store.create_worker(
        _uid("w-dead4"), _uid("task-4"), kind="internal", target_id=session, state="deleted"
    )
    worker_store.create_worker(
        _uid("w-live4"), _uid("task-4"), kind="internal", target_id=session, state="idle"
    )

    found = worker_store.get_by_target_id(session)
    assert found is not None
    assert found.id == _uid("w-live4")


def test_get_by_target_id_none_when_all_inactive(
    worker_store: SqlAlchemyWorkerStore,
) -> None:
    """A session whose only rows are terminated/deleted reads as unbound."""
    session = _uid("sess-5")
    worker_store.create_worker(
        _uid("w-dead5"), _uid("task-5"), kind="internal", target_id=session, state="deleted"
    )
    assert worker_store.get_by_target_id(session) is None
