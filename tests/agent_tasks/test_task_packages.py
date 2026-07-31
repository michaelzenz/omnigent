"""Tests for pending task packages and event reconcile."""

from __future__ import annotations

import uuid

import pytest

from omnigent.agent_tasks.secretary_inbox import build_ambiguous_inbox
from omnigent.agent_tasks.task_match import rank_tasks_for_events, routable_tasks
from omnigent.agent_tasks.items import resolve_task_item
from omnigent.agent_tasks.task_packages import (
    PackageItemSpec,
    create_task_package,
    reconcile_events_to_task,
    reject_task_package,
)
from omnigent.db.utils import generate_agent_id
from omnigent.entities import TaskEventTag
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
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
        "conversation": SqlAlchemyConversationStore(db_uri),
    }


def test_routable_tasks_include_paused(stores) -> None:
    task_store = stores["task"]
    manager_id = generate_agent_id()
    stores["agent"].create(manager_id, name="manager", bundle_location="test:///bundle")
    active_id = _uid("active-task")
    paused_id = _uid("paused-task")
    task_store.create(active_id, manager_id, "Active task", state="active")
    task_store.create(paused_id, manager_id, "Paused task", state="pending")
    routable = routable_tasks(task_store)
    assert {task.id for task in routable} == {active_id, paused_id}


def test_rank_tasks_for_events_includes_paused_match(stores) -> None:
    task_store = stores["task"]
    event_store = stores["event"]
    manager_id = generate_agent_id()
    stores["agent"].create(manager_id, name="manager", bundle_location="test:///bundle")
    paused_id = _uid("paused-match")
    task_store.create(
        paused_id,
        manager_id,
        "omnigent-fork",
        state="pending",
        internal_note="repo:omnigent-fork",
    )
    event = event_store.create_event(
        _uid("event-match"),
        "github.pr.checks_failed",
        "PR checks failed",
        state="awaiting_grouping",
        summary="repo:omnigent-fork pr:891",
    )
    ranked = rank_tasks_for_events(
        events=[event],
        tasks=routable_tasks(task_store),
    )
    assert ranked
    assert ranked[0][0].id == paused_id


def test_create_task_package_reconciles_events(stores) -> None:
    task_store = stores["task"]
    event_store = stores["event"]
    item_store = stores["item"]
    manager_id = generate_agent_id()
    stores["agent"].create(manager_id, name="manager", bundle_location="test:///bundle")
    event_id = _uid("package-event")
    event_store.create_event(
        event_id,
        "github.pr.checks_failed",
        "PR checks failed",
        state="awaiting_grouping",
        summary="repo:acme/widgets pr:123",
        tags=[
            TaskEventTag(event_id=event_id, tag_type="repo", tag="acme/widgets"),
        ],
    )

    task = create_task_package(
        owner_user_id=_uid("owner"),
        manager_agent_id=manager_id,
        title="CI failure on PR #123",
        items=[
            PackageItemSpec(
                title="Investigate CI failure",
                event_ids=[event_id],
                instructions="Check workflow logs",
            ),
        ],
        task_store=task_store,
        task_item_store=item_store,
        task_event_store=event_store,
    )
    assert task.state == "pending"
    items = item_store.list_items_for_task(task.id, state="awaiting_user_ack")
    assert len(items) == 1
    event = event_store.get_event(event_id)
    assert event is not None
    assert event.state == "reconciled"


def test_reconcile_events_extends_paused_package_item(stores) -> None:
    task_store = stores["task"]
    event_store = stores["event"]
    item_store = stores["item"]
    manager_id = generate_agent_id()
    stores["agent"].create(manager_id, name="manager", bundle_location="test:///bundle")
    e1 = _uid("extend-e1")
    e2 = _uid("extend-e2")
    event_store.create_event(
        e1,
        "github.pr.checks_failed",
        "checks failed",
        state="awaiting_grouping",
        source="poll",
        source_key="org/repo#891",
    )
    event_store.create_event(
        e2,
        "github.pr.review_comment",
        "new comment",
        state="awaiting_grouping",
        source="poll",
        source_key="org/repo#891",
    )
    task = create_task_package(
        owner_user_id=_uid("owner"),
        manager_agent_id=manager_id,
        title="PR 891",
        items=[PackageItemSpec(title="Fix PR 891", event_ids=[e1])],
        task_store=task_store,
        task_item_store=item_store,
        task_event_store=event_store,
    )
    first_item = item_store.list_items_for_task(task.id)[0]
    extended = reconcile_events_to_task(
        task=task,
        spec=PackageItemSpec(
            title="Fix PR 891",
            event_ids=[e2],
            item_id=first_item.id,
        ),
        task_item_store=item_store,
        task_event_store=event_store,
    )
    assert extended is not None
    assert extended.id == first_item.id
    links = item_store.list_events_for_item(first_item.id)
    assert {link.event_id for link in links} == {e1, e2}


def test_resolve_inbox_item_activates_paused_package(stores) -> None:
    task_store = stores["task"]
    event_store = stores["event"]
    item_store = stores["item"]
    agent_store = stores["agent"]
    conversation_store = stores["conversation"]
    manager_id = generate_agent_id()
    worker_id = generate_agent_id()
    agent_store.create(manager_id, name="manager", bundle_location="test:///bundle")
    agent_store.create(worker_id, name="worker", bundle_location="test:///bundle")
    event_id = _uid("resolve-event")
    event_store.create_event(
        event_id,
        "github.pr.checks_failed",
        "PR checks failed",
        state="awaiting_grouping",
    )
    task = create_task_package(
        owner_user_id=_uid("owner"),
        manager_agent_id=manager_id,
        title="Package to activate",
        items=[PackageItemSpec(title="Do work", event_ids=[event_id], instructions="Do the work")],
        task_store=task_store,
        task_item_store=item_store,
        task_event_store=event_store,
    )
    item = item_store.list_items_for_task(task.id, state="awaiting_user_ack")[0]
    updated, execution = resolve_task_item(
        item=item,
        resolution="edit_and_dispatch",
        task=task,
        task_store=task_store,
        task_item_store=item_store,
        task_event_store=event_store,
        conversation_store=conversation_store,
        agent_store=agent_store,
        edited_payload={
            "worker_agent_id": worker_id,
            "host_id": _uid("host"),
            "workspace": "/tmp/omnigent-task-test",
            "harness": "cursor",
            "model": "composer-2.5",
        },
    )
    assert updated.state == "running"
    assert execution is not None
    activated = task_store.get(task.id)
    assert activated is not None
    assert activated.state == "active"
    assert activated.manager_conversation_id is not None


def test_skip_inbox_items_keeps_paused_task(stores) -> None:
    task_store = stores["task"]
    event_store = stores["event"]
    item_store = stores["item"]
    agent_store = stores["agent"]
    conversation_store = stores["conversation"]
    manager_id = generate_agent_id()
    agent_store.create(manager_id, name="manager", bundle_location="test:///bundle")
    event_ids = [_uid("skip-e1"), _uid("skip-e2")]
    for event_id in event_ids:
        event_store.create_event(
            event_id,
            "github.pr.checks_failed",
            "PR checks failed",
            state="awaiting_grouping",
        )
    task = create_task_package(
        owner_user_id=_uid("owner"),
        manager_agent_id=manager_id,
        title="Package to skip",
        items=[
            PackageItemSpec(title="Skip me", event_ids=[event_ids[0]]),
            PackageItemSpec(title="Skip me too", event_ids=[event_ids[1]]),
        ],
        task_store=task_store,
        task_item_store=item_store,
        task_event_store=event_store,
    )
    for item in item_store.list_items_for_task(task.id, state="awaiting_user_ack"):
        updated, execution = resolve_task_item(
            item=item,
            resolution="reject_item",
            task=task,
            task_store=task_store,
            task_item_store=item_store,
            task_event_store=event_store,
            conversation_store=conversation_store,
            agent_store=agent_store,
        )
        assert updated.state == "cancelled"
        assert execution is None

    unchanged = task_store.get(task.id)
    assert unchanged is not None
    assert unchanged.state == "pending"


def test_reject_task_package(stores) -> None:
    task_store = stores["task"]
    event_store = stores["event"]
    item_store = stores["item"]
    agent_store = stores["agent"]
    manager_id = generate_agent_id()
    agent_store.create(manager_id, name="manager", bundle_location="test:///bundle")
    reject_event_id = _uid("reject-event")
    event_store.create_event(
        reject_event_id,
        "github.pr.checks_failed",
        "another failure",
        state="awaiting_grouping",
    )
    reject_task = create_task_package(
        owner_user_id=_uid("owner"),
        manager_agent_id=manager_id,
        title="Package to reject",
        items=[PackageItemSpec(title="Do work", event_ids=[reject_event_id])],
        task_store=task_store,
        task_item_store=item_store,
        task_event_store=event_store,
    )
    archived = reject_task_package(
        task=reject_task,
        task_store=task_store,
        task_item_store=item_store,
        task_event_store=event_store,
    )
    assert archived.state == "archived"
    released = event_store.get_event(reject_event_id)
    assert released is not None
    assert released.state == "awaiting_grouping"


def test_ambiguous_inbox_suggests_paused_tasks(stores) -> None:
    task_store = stores["task"]
    event_store = stores["event"]
    item_store = stores["item"]
    manager_id = generate_agent_id()
    stores["agent"].create(manager_id, name="manager", bundle_location="test:///bundle")
    paused_id = _uid("paused-inbox")
    task_store.create(
        paused_id,
        manager_id,
        "omnigent-fork",
        state="pending",
        internal_note="repo:omnigent-fork",
    )
    event_id = _uid("inbox-event")
    event_store.create_event(
        event_id,
        "github.pr.checks_failed",
        "PR checks failed",
        state="awaiting_grouping",
        summary="repo:omnigent-fork pr:891",
    )
    inbox = build_ambiguous_inbox(
        task_event_store=event_store,
        task_item_store=item_store,
        task_store=task_store,
    )
    assert inbox["clusters"]
    candidates = inbox["clusters"][0]["suggested_candidates"]
    assert candidates
    assert candidates[0]["task_id"] == paused_id
    assert candidates[0]["state"] == "pending"
