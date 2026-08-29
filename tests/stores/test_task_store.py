"""Tests for :class:`SqlAlchemyTaskStore`."""

from __future__ import annotations

import uuid

import pytest

from omnigent.entities import TaskTag
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.fixture()
def store(db_uri: str) -> SqlAlchemyTaskStore:
    return SqlAlchemyTaskStore(db_uri)


def test_create_and_get_round_trip(store: SqlAlchemyTaskStore) -> None:
    task = store.create(
        task_id=_uid("task_1"),
        title="S3 reliability",
        goal="S3 uploads are reliable",
        owner_user_id="alice@example.com",
        internal_note="upload retries and backoff",
        manager_conversation_id=_uid("conv_mgr"),
        tags=[TaskTag(task_id=_uid("task_1"), tag_type="domain", tag="s3")],
    )
    assert task.id == _uid("task_1")
    assert task.manager_conversation_id == _uid("conv_mgr")
    # A task names the roles that run it, not the agent profiles behind them.
    assert task.manager_role_key == "manager:default"
    assert task.worker_role_key == "worker:default"
    loaded = store.get(_uid("task_1"))
    assert loaded == task


def test_create_accepts_custom_role_keys(store: SqlAlchemyTaskStore) -> None:
    """Manager and worker lanes can be pointed at custom glossary roles."""
    task_id = _uid("task_roles")
    store.create(
        task_id=task_id,
        title="Research spike",
        goal="Research spike complete",
        manager_role_key="manager:research",
        worker_role_key="worker:reviewer",
    )
    loaded = store.get(task_id)
    assert loaded is not None
    assert loaded.manager_role_key == "manager:research"
    assert loaded.worker_role_key == "worker:reviewer"
    assert store.count_by_manager_role_key("manager:research") == 1
    assert store.count_by_worker_role_key("worker:reviewer") == 1


def test_set_tags_replaces_task_tags(store: SqlAlchemyTaskStore) -> None:
    task_id = _uid("task_tags")
    store.create(
        task_id=task_id,
        title="Title",
        goal="goal",
        internal_note="routing context",
    )
    store.set_tags(
        task_id,
        [
            TaskTag(task_id=task_id, tag_type="domain", tag="ci"),
            TaskTag(task_id=task_id, tag_type="component", tag="build"),
        ],
    )
    tags = store.get_tags(task_id)
    assert {(tag.tag_type, tag.tag) for tag in tags} == {
        ("domain", "ci"),
        ("component", "build"),
    }


def test_list_task_ids_by_tag(store: SqlAlchemyTaskStore) -> None:
    task_a = _uid("task_a")
    task_b = _uid("task_b")
    store.create(task_id=task_a, title="A", goal="a goal")
    store.create(task_id=task_b, title="B", goal="b goal")
    store.set_tags(task_a, [TaskTag(task_id=task_a, tag_type="domain", tag="s3")])
    store.set_tags(task_b, [TaskTag(task_id=task_b, tag_type="domain", tag="s3")])
    assert sorted(store.list_task_ids_by_tag("domain", "s3")) == sorted([task_a, task_b])


def test_delete_removes_tags_and_workers(store: SqlAlchemyTaskStore) -> None:
    from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore

    task_id = _uid("task_delete")
    session_id = _uid("sess_delete")
    store.create(task_id=task_id, title="Delete me", goal="deleted")
    store.set_tags(task_id, [TaskTag(task_id=task_id, tag_type="domain", tag="x")])
    worker_store = SqlAlchemyWorkerStore(store.storage_location)
    worker_store.create_worker(
        _uid("worker_delete"),
        task_id,
        role_key="worker:default",
        session_id=session_id,
    )
    assert store.delete(task_id) is True
    assert store.get(task_id) is None
    assert store.get_tags(task_id) == []
    assert worker_store.get_by_session_id(session_id) is None


def test_list_recent_orders_by_last_touch(store: SqlAlchemyTaskStore) -> None:
    old = _uid("task_old")
    bumped = _uid("task_bumped")
    store.create(task_id=old, title="Old", goal="old goal")
    store.create(task_id=bumped, title="Bumped", goal="bumped goal")
    store.update(bumped, title="Bumped!")
    recent = store.list_recent(5)
    assert recent[0].id == bumped
    assert {task.id for task in recent} == {old, bumped}


def test_list_recent_has_no_state_filter(store: SqlAlchemyTaskStore) -> None:
    archived = _uid("task_archived")
    store.create(task_id=archived, title="Archived", goal="gone")
    store.update(archived, state="archived")
    recent = store.list_recent(5)
    assert any(task.id == archived for task in recent)


def test_list_recent_respects_limit(store: SqlAlchemyTaskStore) -> None:
    for i in range(5):
        store.create(task_id=_uid(f"task_lim_{i}"), title=f"T{i}", goal="g")
    assert len(store.list_recent(3)) == 3
