"""Tests for secretary task-item routing proposals."""

from __future__ import annotations

import uuid

import pytest

from omnigent.agent_tasks.routing_proposals import (
    cluster_ambiguous_events,
    derive_cluster_key,
    upsert_routing_proposal,
)
from omnigent.db.utils import generate_agent_id
from omnigent.entities import TaskEvent, TaskEventTag
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.fixture
def stores(db_uri: str):
    return {
        "task": SqlAlchemyTaskStore(db_uri),
        "event": SqlAlchemyTaskEventStore(db_uri),
        "item": SqlAlchemyTaskItemStore(db_uri),
        "agent": SqlAlchemyAgentStore(db_uri),
    }


def test_derive_cluster_key_from_tags() -> None:
    event = TaskEvent(
        id="e1",
        event_type="github.pr.checks_failed",
        title="PR failed",
        search_text="github.pr.checks_failed\nPR failed",
        state="awaiting_grouping",
        priority=0,
        created_at=1,
        source="poll_plugin:github_pr",
        source_key="org/repo#891",
    )
    tags = [
        TaskEventTag(event_id="e1", tag_type="repo", tag="org/repo"),
        TaskEventTag(event_id="e1", tag_type="pr", tag="891"),
    ]
    assert derive_cluster_key(event, tags) == "pr:org/repo#891"


def test_cluster_ambiguous_events_groups_by_pr(stores) -> None:
    event_store = stores["event"]
    e1 = event_store.create_event(
        _uid("e1"),
        "github.pr.checks_failed",
        "checks failed",
        state="awaiting_grouping",
        source="poll",
        source_key="org/repo#891",
        tags=[
            TaskEventTag(event_id=_uid("e1"), tag_type="repo", tag="org/repo"),
            TaskEventTag(event_id=_uid("e1"), tag_type="pr", tag="891"),
        ],
    )
    e2 = event_store.create_event(
        _uid("e2"),
        "github.pr.review_comment",
        "new comment",
        state="awaiting_grouping",
        source="poll",
        source_key="org/repo#891",
        tags=[
            TaskEventTag(event_id=_uid("e2"), tag_type="repo", tag="org/repo"),
            TaskEventTag(event_id=_uid("e2"), tag_type="pr", tag="891"),
        ],
    )
    tags_by_id = {
        e1.id: event_store.get_event_tags(e1.id),
        e2.id: event_store.get_event_tags(e2.id),
    }
    clusters = cluster_ambiguous_events([e1, e2], tags_by_event_id=tags_by_id)
    assert len(clusters) == 1
    assert len(clusters[0].events) == 2
    assert clusters[0].suggested_canonical_key == "pr:org/repo#891"


def test_upsert_appends_to_open_proposal(stores) -> None:
    task_store = stores["task"]
    event_store = stores["event"]
    item_store = stores["item"]
    agent_store = stores["agent"]

    manager_id = generate_agent_id()
    worker_id = generate_agent_id()
    agent_store.create(manager_id, name="manager", bundle_location="test:///bundle")
    agent_store.create(worker_id, name="worker", bundle_location="test:///bundle")

    task_id = _uid("task")
    task_store.create(task_id, manager_id, "omnigent-fork", charter="repo:omnigent-fork")

    e1 = event_store.create_event(
        _uid("ev1"),
        "github.pr.checks_failed",
        "checks failed",
        state="awaiting_grouping",
        source="poll",
        source_key="org/repo#891",
        summary="repo:org/repo pr:891",
        tags=[
            TaskEventTag(event_id=_uid("ev1"), tag_type="repo", tag="org/repo"),
            TaskEventTag(event_id=_uid("ev1"), tag_type="pr", tag="891"),
        ],
    )
    first = upsert_routing_proposal(
        owner_user_id="user1",
        canonical_key="pr:org/repo#891",
        title="Fix PR 891",
        event_ids=[e1.id],
        suggested_task_id=task_id,
        task_store=task_store,
        task_item_store=item_store,
        task_event_store=event_store,
        instructions="Fix lint",
        worker_agent_id=worker_id,
        model="composer-2.5",
        harness="cursor-native",
        host_id="host1",
        workspace="/tmp/ws",
    )
    assert first is not None
    assert first.state == "routing_proposed"

    e2 = event_store.create_event(
        _uid("ev2"),
        "github.pr.review_comment",
        "new comment",
        state="awaiting_grouping",
        source="poll",
        source_key="org/repo#891",
        summary="repo:org/repo pr:891",
        tags=[
            TaskEventTag(event_id=_uid("ev2"), tag_type="repo", tag="org/repo"),
            TaskEventTag(event_id=_uid("ev2"), tag_type="pr", tag="891"),
        ],
    )
    second = upsert_routing_proposal(
        owner_user_id="user1",
        canonical_key="pr:org/repo#891",
        title="Fix PR 891",
        event_ids=[e2.id],
        suggested_task_id=task_id,
        task_store=task_store,
        task_item_store=item_store,
        task_event_store=event_store,
        instructions="Fix lint and address comment",
        worker_agent_id=worker_id,
        model="composer-2.5",
        harness="cursor-native",
        host_id="host1",
        workspace="/tmp/ws",
    )
    assert second is not None
    assert second.id == first.id
    linked = item_store.list_events_for_item(first.id)
    assert len(linked) == 2
