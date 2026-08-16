"""Tests for pending task packages and event reconcile."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from omnigent.agent_tasks.broker_inbox import build_ambiguous_inbox
from omnigent.agent_tasks.items import resolve_task_item
from omnigent.agent_tasks.role_keys import WORKER_DEFAULT_ROLE_KEY
from omnigent.agent_tasks.task_match import rank_tasks_for_events, routable_tasks
from omnigent.agent_tasks.task_packages import (
    PackageItemSpec,
    accept_task_package,
    create_task_package,
    reconcile_events_to_task,
    reconcile_events_to_task_batch,
    reject_task_package,
)
from omnigent.db.utils import generate_agent_id
from omnigent.entities import EventTag, TaskTag
from omnigent.entities.task_role_profile import TaskRoleProfile
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore
from omnigent.stores.task_role_profile_store.sqlalchemy_store import SqlAlchemyTaskRoleProfileStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.fixture
def stores(db_uri: str):
    return {
        "task": SqlAlchemyTaskStore(db_uri),
        "event": SqlAlchemyTaskEventStore(db_uri),
        "item": SqlAlchemyTaskItemStore(db_uri),
        "worker": SqlAlchemyWorkerStore(db_uri),
        "agent": SqlAlchemyAgentStore(db_uri),
        "conversation": SqlAlchemyConversationStore(db_uri),
    }


def test_routable_tasks_include_paused(stores) -> None:
    task_store = stores["task"]
    active_id = _uid("active-task")
    paused_id = _uid("paused-task")
    task_store.create(active_id, "Active task", "active goal", state="active")
    task_store.create(paused_id, "Paused task", "paused goal", state="pending")
    routable = routable_tasks(task_store)
    assert {task.id for task in routable} == {active_id, paused_id}


def test_rank_tasks_for_events_includes_paused_match(stores) -> None:
    task_store = stores["task"]
    event_store = stores["event"]
    paused_id = _uid("paused-match")
    task_store.create(
        paused_id,
        "Paused match",
        "paused goal",
        state="pending",
        internal_note="repo:omnigent-fork",
        tags=[TaskTag(task_id=paused_id, tag_type="repo", tag="omnigent-fork")],
    )
    event = event_store.create_event(
        _uid("event-match"),
        "github.pr.checks_failed",
        "PR checks failed",
        state="awaiting_grouping",
        tags=[
            EventTag(tag_type="repo", tag="omnigent-fork"),
        ],
    )
    ranked = rank_tasks_for_events(
        events=[event],
        tasks=routable_tasks(task_store),
        task_store=task_store,
    )
    assert ranked
    assert ranked[0][0].id == paused_id


def test_create_task_package_reconciles_events(stores) -> None:
    task_store = stores["task"]
    event_store = stores["event"]
    item_store = stores["item"]
    event_id = _uid("package-event")
    event_store.create_event(
        event_id,
        "github.pr.checks_failed",
        "PR checks failed",
        state="awaiting_grouping",
        tags=[
            EventTag(tag_type="repo", tag="acme/widgets"),
        ],
    )

    task = create_task_package(
        owner_user_id=_uid("owner"),
        title="CI failure on PR #123",
        goal="PR #123 CI is green",
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
        worker_store=stores["worker"],
    )
    assert task.state == "pending"
    items = item_store.list_items_for_task(task.id, state="pending")
    assert len(items) == 1
    event = event_store.get_event(event_id)
    assert event is not None
    assert event.state == "reconciled"


def test_reconcile_events_extends_paused_package_item(stores) -> None:
    task_store = stores["task"]
    event_store = stores["event"]
    item_store = stores["item"]
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
        title="PR 891",
        goal="PR 891 lands",
        items=[PackageItemSpec(title="Fix PR 891", event_ids=[e1])],
        task_store=task_store,
        task_item_store=item_store,
        task_event_store=event_store,
        worker_store=stores["worker"],
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
        worker_store=stores["worker"],
    )
    assert extended is not None
    assert extended.id == first_item.id
    links = item_store.list_events_for_item(first_item.id)
    assert {link.event_id for link in links} == {e1, e2}


def test_reconcile_events_batch_dedups_shared_event(stores) -> None:
    """A shared event is claimed by the first spec; the second spec skips it."""
    task_store = stores["task"]
    event_store = stores["event"]
    item_store = stores["item"]
    e1, e2, e3 = (_uid(f"dedup-{i}") for i in range(3))
    for eid, etype in ((e1, "build.finished"), (e2, "build.failed"), (e3, "build.finished")):
        event_store.create_event(eid, etype, "flaky", state="awaiting_grouping")

    task = task_store.create(
        _uid("dedup-task"),
        "Dedup package",
        "dedup goal",
        owner_user_id=_uid("owner"),
        state="pending",
    )
    results = reconcile_events_to_task_batch(
        task=task,
        specs=[
            PackageItemSpec(title="A", event_ids=[e1, e2]),
            PackageItemSpec(title="B", event_ids=[e2, e3]),
        ],
        task_item_store=item_store,
        task_event_store=event_store,
        worker_store=stores["worker"],
    )
    assert all(results)
    item_a, item_b = results
    assert {link.event_id for link in item_store.list_events_for_item(item_a.id)} == {e1, e2}
    # e2 was consumed by A, so B only gets e3.
    assert {link.event_id for link in item_store.list_events_for_item(item_b.id)} == {e3}
    for eid in (e1, e2, e3):
        assert event_store.get_event(eid).state == "reconciled"


@pytest.mark.asyncio
async def test_resolve_inbox_item_activates_accepted_package(stores, db_uri: str) -> None:
    task_store = stores["task"]
    event_store = stores["event"]
    item_store = stores["item"]
    agent_store = stores["agent"]
    conversation_store = stores["conversation"]
    profile_store = SqlAlchemyTaskRoleProfileStore(db_uri)
    manager_agent_id = generate_agent_id()
    agent_store.create(manager_agent_id, name="task-manager", bundle_location="test:///bundle")
    profile_store.upsert(
        "manager:default",
        kind="manager",
        agent_profile_id=manager_agent_id,
    )
    worker_agent_id = generate_agent_id()
    agent_store.create(worker_agent_id, name="worker", bundle_location="test:///bundle")
    event_id = _uid("resolve-event")
    event_store.create_event(
        event_id,
        "github.pr.checks_failed",
        "PR checks failed",
        state="awaiting_grouping",
    )
    pending_task = create_task_package(
        owner_user_id=_uid("owner"),
        title="Package to activate",
        goal="Package activated and work done",
        items=[PackageItemSpec(title="Do work", event_ids=[event_id], instructions="Do the work")],
        task_store=task_store,
        task_item_store=item_store,
        task_event_store=event_store,
        worker_store=stores["worker"],
    )
    task = accept_task_package(
        task=pending_task,
        task_store=task_store,
        task_role_profile_store=profile_store,
    )
    worker_store = stores["worker"]
    item = item_store.list_items_for_task(task.id, state="pending")[0]

    # Assign a worker lane before resolving (manager sweep).
    worker = worker_store.create_worker(
        uuid.uuid4().hex,
        task.id,
        role_key=WORKER_DEFAULT_ROLE_KEY,
        kind="managed",
    )
    item_store.update_item(item.id, worker_id=worker.id)

    async def _mock_session_creator(*, body, request, user_id, **kwargs):
        return conversation_store.create_conversation(
            title=body.title or "Task manager",
            agent_id=body.agent_id,
            host_id=body.host_id,
            workspace=body.workspace,
        )

    updated, execution = await resolve_task_item(
        item=item,
        resolution="edit_and_dispatch",
        task=task,
        task_store=task_store,
        task_item_store=item_store,
        task_event_store=event_store,
        worker_store=worker_store,
        conversation_store=conversation_store,
        role_profile=TaskRoleProfile(
            role=WORKER_DEFAULT_ROLE_KEY,
            kind="worker",
            agent_profile_id=worker_agent_id,
            harness="cursor",
            model="composer-2.5",
            host_id=_uid("host"),
            workspace="/tmp/omnigent-task-test",
            created_at=1,
        ),
        edited_payload={
            "host_id": _uid("host"),
            "workspace": "/tmp/omnigent-task-test",
            "harness": "cursor",
            "model": "composer-2.5",
        },
        session_creator=_mock_session_creator,
        app_state=SimpleNamespace(),
    )
    assert updated.state == "running"
    assert execution is not None
    activated = task_store.get(task.id)
    assert activated is not None
    assert activated.state == "active"
    assert activated.manager_conversation_id is not None


@pytest.mark.asyncio
async def test_skip_inbox_items_keeps_paused_task(stores) -> None:
    task_store = stores["task"]
    event_store = stores["event"]
    item_store = stores["item"]
    conversation_store = stores["conversation"]
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
        title="Package to skip",
        goal="Package skipped",
        items=[
            PackageItemSpec(title="Skip me", event_ids=[event_ids[0]]),
            PackageItemSpec(title="Skip me too", event_ids=[event_ids[1]]),
        ],
        task_store=task_store,
        task_item_store=item_store,
        task_event_store=event_store,
        worker_store=stores["worker"],
    )
    worker_store = stores["worker"]
    for item in item_store.list_items_for_task(task.id, state="pending"):
        updated, execution = await resolve_task_item(
            item=item,
            resolution="reject_item",
            task=task,
            task_store=task_store,
            task_item_store=item_store,
            task_event_store=event_store,
            worker_store=worker_store,
            conversation_store=conversation_store,
        )
        assert updated.state == "cancelled"
        assert execution is None

    unchanged = task_store.get(task.id)
    assert unchanged is not None
    assert unchanged.state == "pending"


def test_accept_task_package(stores, db_uri: str) -> None:
    task_store = stores["task"]
    event_store = stores["event"]
    item_store = stores["item"]
    agent_store = stores["agent"]
    profile_store = SqlAlchemyTaskRoleProfileStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="task-manager", bundle_location="test:///bundle")
    profile_store.upsert(
        "manager:default",
        kind="manager",
        agent_profile_id=agent_id,
    )
    event_id = _uid("accept-event")
    event_store.create_event(
        event_id,
        "github.pr.checks_failed",
        "failure",
        state="awaiting_grouping",
    )
    pending_task = create_task_package(
        owner_user_id=_uid("owner"),
        title="Package to accept",
        goal="Package accepted",
        items=[PackageItemSpec(title="Do work", event_ids=[event_id])],
        task_store=task_store,
        task_item_store=item_store,
        task_event_store=event_store,
        worker_store=stores["worker"],
    )
    accepted = accept_task_package(
        task=pending_task,
        task_store=task_store,
        task_role_profile_store=profile_store,
    )
    assert accepted.state == "idle"


def test_reject_task_package(stores) -> None:
    task_store = stores["task"]
    event_store = stores["event"]
    item_store = stores["item"]
    reject_event_id = _uid("reject-event")
    event_store.create_event(
        reject_event_id,
        "github.pr.checks_failed",
        "another failure",
        state="awaiting_grouping",
    )
    reject_task = create_task_package(
        owner_user_id=_uid("owner"),
        title="Package to reject",
        goal="Package rejected",
        items=[PackageItemSpec(title="Do work", event_ids=[reject_event_id])],
        task_store=task_store,
        task_item_store=item_store,
        task_event_store=event_store,
        worker_store=stores["worker"],
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
    paused_id = _uid("paused-inbox")
    task_store.create(
        paused_id,
        "Paused match",
        "paused goal",
        state="pending",
        internal_note="repo:omnigent-fork",
        tags=[TaskTag(task_id=paused_id, tag_type="repo", tag="omnigent-fork")],
    )
    event_id = _uid("inbox-event")
    event_store.create_event(
        event_id,
        "github.pr.checks_failed",
        "PR checks failed",
        state="awaiting_grouping",
        tags=[
            EventTag(tag_type="repo", tag="omnigent-fork"),
        ],
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
