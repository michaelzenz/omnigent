"""Tests for :class:`SqlAlchemyTaskEventStore`."""

from __future__ import annotations

import uuid

import pytest

from omnigent.entities import TaskEventTag
from omnigent.stores.task_event_store import TASK_SESSION_BINDING_KINDS
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.fixture()
def store(db_uri: str) -> SqlAlchemyTaskEventStore:
    return SqlAlchemyTaskEventStore(db_uri)


def test_create_event_and_get_by_source_dedupes(store: SqlAlchemyTaskEventStore) -> None:
    event_id = _uid("event_1")
    created = store.create_event(
        event_id=event_id,
        event_type="build.finished",
        title="Build passed",
        source="ci",
        source_key="build-42",
        source_offset=100,
        tags=[TaskEventTag(event_id=event_id, tag_type="domain", tag="ci")],
    )
    assert created.source_key == "build-42"
    assert created.source_offset == 100
    loaded = store.get_event_by_source(
        source="ci",
        source_key="build-42",
        source_offset=100,
        event_type="build.finished",
    )
    assert loaded is not None
    assert loaded.id == event_id
    assert loaded.tags == [
        TaskEventTag(event_id=event_id, tag_type="domain", tag="ci"),
    ]


def test_routing_attempts_and_resolution(store: SqlAlchemyTaskEventStore) -> None:
    event_id = _uid("event_route")
    attempt_id = _uid("attempt_1")
    store.create_event(event_id=event_id, event_type="note.added", title="New note")
    attempt = store.create_routing_attempt(
        attempt_id=attempt_id,
        event_id=event_id,
        candidate_task_id=_uid("task_1"),
        candidate_manager_agent_id=_uid("mgr_1"),
        rank=1,
        decision="accepted",
    )
    assert attempt.decision == "accepted"
    attempts = store.list_routing_attempts(event_id)
    assert len(attempts) == 1
    resolution = store.create_resolution(
        resolution_id=_uid("resolution_1"),
        event_id=event_id,
        selected_attempt_id=attempt_id,
        selected_task_id=_uid("task_1"),
        selected_manager_agent_id=_uid("mgr_1"),
        resolved_by_user_id="alice@example.com",
    )
    assert store.get_resolution(event_id) == resolution


def test_execution_lookup_by_conversation_id(store: SqlAlchemyTaskEventStore) -> None:
    event_id = _uid("event_exec")
    execution_id = _uid("exec_1")
    task_item_id = _uid("item_exec")
    conversation_id = _uid("worker_conv")
    store.create_event(
        event_id=event_id,
        event_type="worker.execution.finished",
        title="Worker done",
        task_id=_uid("task_1"),
        manager_agent_id=_uid("mgr_1"),
        state="routed",
    )
    store.create_execution(
        execution_id=execution_id,
        task_item_id=task_item_id,
        event_id=event_id,
        task_id=_uid("task_1"),
        manager_agent_id=_uid("mgr_1"),
        worker_agent_id=_uid("worker_1"),
        conversation_id=conversation_id,
        status="running",
    )
    loaded = store.get_execution_by_conversation_id(conversation_id)
    assert loaded is not None
    assert loaded.id == execution_id
    updated = store.update_execution(
        execution_id,
        status="succeeded",
        finished_at=1_700_000_000,
        result_summary="done",
    )
    assert updated is not None
    assert updated.status == "succeeded"


def test_session_binding_upsert_and_get(store: SqlAlchemyTaskEventStore) -> None:
    session_id = _uid("sess_bind")
    binding = store.upsert_binding(
        session_id,
        _uid("task_1"),
        _uid("mgr_1"),
        "ambient",
        manager_conversation_id=_uid("conv_mgr"),
    )
    assert binding.binding_kind == "ambient"
    loaded = store.get_binding(session_id)
    assert loaded == binding
    replaced = store.upsert_binding(
        session_id,
        _uid("task_2"),
        _uid("mgr_2"),
        "worker",
    )
    assert replaced.task_id == _uid("task_2")
    assert store.delete_binding(session_id) is True
    assert store.get_binding(session_id) is None


def test_unknown_binding_kind_raises(store: SqlAlchemyTaskEventStore) -> None:
    with pytest.raises(ValueError, match="unknown binding_kind"):
        store.upsert_binding(_uid("sess"), _uid("task"), _uid("mgr"), "invalid")
    assert "manager" in TASK_SESSION_BINDING_KINDS
