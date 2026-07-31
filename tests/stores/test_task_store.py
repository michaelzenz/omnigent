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
        manager_agent_id=_uid("mgr_1"),
        title="S3 reliability",
        owner_user_id="alice@example.com",
        internal_note="upload retries and backoff",
        manager_conversation_id=_uid("conv_mgr"),
        tags=[TaskTag(task_id=_uid("task_1"), tag_type="domain", tag="s3")],
    )
    assert task.id == _uid("task_1")
    assert task.manager_conversation_id == _uid("conv_mgr")
    assert "domain:s3" in task.search_text
    loaded = store.get(_uid("task_1"))
    assert loaded == task


def test_set_tags_rebuilds_search_text(store: SqlAlchemyTaskStore) -> None:
    task_id = _uid("task_tags")
    store.create(
        task_id=task_id,
        manager_agent_id=_uid("mgr_1"),
        title="Title",
    )
    store.set_tags(
        task_id,
        [
            TaskTag(task_id=task_id, tag_type="domain", tag="ci"),
            TaskTag(task_id=task_id, tag_type="component", tag="build"),
        ],
    )
    task = store.get(task_id)
    assert task is not None
    assert "domain:ci" in task.search_text
    assert "component:build" in task.search_text


def test_list_task_ids_by_tag(store: SqlAlchemyTaskStore) -> None:
    task_a = _uid("task_a")
    task_b = _uid("task_b")
    store.create(task_id=task_a, manager_agent_id=_uid("mgr"), title="A")
    store.create(task_id=task_b, manager_agent_id=_uid("mgr"), title="B")
    store.set_tags(task_a, [TaskTag(task_id=task_a, tag_type="domain", tag="s3")])
    store.set_tags(task_b, [TaskTag(task_id=task_b, tag_type="domain", tag="s3")])
    assert sorted(store.list_task_ids_by_tag("domain", "s3")) == sorted([task_a, task_b])


def test_search_tasks(store: SqlAlchemyTaskStore) -> None:
    store.create(
        task_id=_uid("task_search"),
        manager_agent_id=_uid("mgr"),
        title="Upload retry work",
        internal_note="backoff for flaky uploads",
    )
    hits = store.search("flaky")
    assert len(hits) == 1
    assert hits[0].id == _uid("task_search")


def test_delete_removes_tags_and_bindings(store: SqlAlchemyTaskStore) -> None:
    from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore

    task_id = _uid("task_delete")
    session_id = _uid("sess_delete")
    store.create(task_id=task_id, manager_agent_id=_uid("mgr"), title="Delete me")
    store.set_tags(task_id, [TaskTag(task_id=task_id, tag_type="domain", tag="x")])
    event_store = SqlAlchemyTaskEventStore(store.storage_location)
    event_store.upsert_binding(
        session_id,
        task_id,
        _uid("mgr"),
        "manager",
        manager_conversation_id=_uid("conv_mgr"),
    )
    assert store.delete(task_id) is True
    assert store.get(task_id) is None
    assert store.get_tags(task_id) == []
    assert event_store.get_binding(session_id) is None
