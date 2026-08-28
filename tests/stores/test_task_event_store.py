"""Tests for :class:`SqlAlchemyTaskEventStore`."""

from __future__ import annotations

import uuid

import pytest

from omnigent.entities import EventTag
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
        tags=[EventTag(tag_type="domain", tag="ci")],
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
        EventTag(tag_type="domain", tag="ci"),
    ]


def test_routing_attempts_round_trip(store: SqlAlchemyTaskEventStore) -> None:
    event_id = _uid("event_route")
    attempt_id = _uid("attempt_1")
    store.create_event(event_id=event_id, event_type="note.added", title="New note")
    attempt = store.create_routing_attempt(
        attempt_id=attempt_id,
        event_id=event_id,
        candidate_task_id=_uid("task_1"),
        score=0.75,
        reason="auto-route score=0.7500",
    )
    assert attempt.reason == "auto-route score=0.7500"
    attempts = store.list_routing_attempts(event_id)
    assert len(attempts) == 1
    assert attempts[0].candidate_task_id == _uid("task_1")


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
        state="routed",
    )
    store.create_execution(
        execution_id=execution_id,
        task_item_id=task_item_id,
        task_id=_uid("task_1"),
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


def test_subscription_crud(store: SqlAlchemyTaskEventStore) -> None:
    task_id = _uid("task_sub")
    created = store.create_subscription(
        _uid("sub_1"),
        task_id,
        source="poll_plugin:github_pr",
        source_key="org/repo#456",
        owner_user_id="user-1",
    )
    assert created.task_id == task_id
    assert created.source_key == "org/repo#456"

    # Re-subscribing the same tuple returns the existing row.
    again = store.create_subscription(
        _uid("sub_2"),
        task_id,
        source="poll_plugin:github_pr",
        source_key="org/repo#456",
    )
    assert again.id == created.id

    other = store.create_subscription(
        _uid("sub_3"),
        _uid("task_other"),
        source="poll_plugin:github_pr",
        source_key="org/repo#456",
    )
    matches = store.list_subscriptions(
        source="poll_plugin:github_pr",
        source_key="org/repo#456",
    )
    assert {sub.id for sub in matches} == {created.id, other.id}
    assert [sub.id for sub in store.list_subscriptions_for_task(task_id)] == [created.id]

    assert store.delete_subscription(created.id) is True
    assert store.delete_subscription(created.id) is False
    assert store.get_subscription(created.id) is None
    assert [sub.id for sub in store.list_subscriptions_for_task(task_id)] == []


def test_fanout_copies_do_not_dedup_canonical(store: SqlAlchemyTaskEventStore) -> None:
    canonical = store.create_event(
        _uid("event_canonical"),
        "github.pr.merged",
        "PR merged",
        source="poll_plugin:github_pr",
        source_key="org/repo#456",
        source_offset=1,
    )
    child = store.create_event(
        _uid("event_child"),
        "github.pr.merged",
        "PR merged",
        task_id=_uid("task_1"),
        source="poll_plugin:github_pr",
        source_key="org/repo#456",
        source_offset=1,
        parent_event_id=canonical.id,
        state="routed",
    )
    assert child.parent_event_id == canonical.id

    deduped = store.get_event_by_source(
        source="poll_plugin:github_pr",
        source_key="org/repo#456",
        source_offset=1,
        event_type="github.pr.merged",
    )
    assert deduped is not None
    assert deduped.id == canonical.id

    deliveries = store.list_deliveries_for_event(canonical.id)
    assert [delivery.id for delivery in deliveries] == [child.id]
