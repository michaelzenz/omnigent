"""Tests for the worker queue (phase 4).

Accept no longer launches a worker synchronously: the item moves to
``queued`` and an ``item.dispatch`` queue item is enqueued for the worker
slot. The dispatcher's ``WorkerDispatchHandler`` then spawns the worker
session off the request path. A resume endpoint re-arms a halted slot.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from omnigent.agent_tasks.items import resolve_task_item
from omnigent.agent_tasks.queue.dispatcher import DispatchFailed, DispatchTarget
from omnigent.agent_tasks.queue.handlers import WorkerDispatchHandler
from omnigent.agent_tasks.role_keys import MANAGER_DEFAULT_ROLE_KEY, WORKER_DEFAULT_ROLE_KEY
from omnigent.db.utils import generate_agent_id, now_epoch
from omnigent.entities import AgentQueueItem, AgentQueueKey, Worker
from omnigent.entities.task_role_profile import TaskRoleProfile
from omnigent.server.routes.agent_queues import create_agent_queues_router
from omnigent.stores.agent_queue_store.sqlalchemy_store import SqlAlchemyAgentQueueStore
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore
from omnigent.stores.task_role_profile_store.sqlalchemy_store import (
    SqlAlchemyTaskRoleProfileStore,
)
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


def _worker_role_profile(agent_profile_id: str, *, workspace: str) -> TaskRoleProfile:
    return TaskRoleProfile(
        role=WORKER_DEFAULT_ROLE_KEY,
        kind="worker",
        agent_profile_id=agent_profile_id,
        host_id=_uid("host"),
        workspace=workspace,
        created_at=1,
    )


def _queue_item(
    key: AgentQueueKey,
    *,
    source_id: str,
    payload: dict | None = None,
) -> AgentQueueItem:
    return AgentQueueItem(
        id=_uid("qitem"),
        role=key.role,
        owner_user_id=key.owner_user_id,
        scope_id=key.scope_id,
        kind="item.dispatch",
        state="queued",
        created_at=now_epoch(),
        source_ids=[source_id],
        payload=json.dumps(payload or {}),
        seq=0,
    )


@pytest.fixture
def worker_setup(db_uri: str) -> dict:
    agent_store = SqlAlchemyAgentStore(db_uri)
    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)
    profile_store = SqlAlchemyTaskRoleProfileStore(db_uri)
    queue_store = SqlAlchemyAgentQueueStore(db_uri)

    manager_agent_id = generate_agent_id()
    worker_agent_id = generate_agent_id()
    agent_store.create(manager_agent_id, name="task-manager-agent", bundle_location="test:///b")
    agent_store.create(worker_agent_id, name="worker-profile", bundle_location="test:///b")
    profile_store.upsert(
        MANAGER_DEFAULT_ROLE_KEY,
        agent_profile_id=manager_agent_id,
        host_id=_uid("host"),
        workspace="/tmp/mgr",
    )
    profile_store.upsert(
        WORKER_DEFAULT_ROLE_KEY,
        agent_profile_id=worker_agent_id,
        host_id=_uid("host"),
        workspace="/tmp/worker",
    )

    manager_conv = conversation_store.create_conversation(
        title="Manager",
        agent_id=manager_agent_id,
        host_id=_uid("host"),
        workspace="/tmp/mgr",
    )
    task_id = _uid("task_w")
    task_store.create(
        task_id,
        "Worker task",
        "worker goal",
        owner_user_id="user-w",
        manager_conversation_id=manager_conv.id,
    )
    worker = worker_store.create_worker(
        _uid("worker"),
        task_id,
    )
    item = item_store.create_item(
        _uid("item"),
        task_id,
        "Do work",
        state="queued",
        instructions="Do the work",
        worker_id=worker.id,
    )
    ensure_runner = AsyncMock()

    async def _mock_session_creator(*, body, request, user_id, **kwargs):
        return conversation_store.create_conversation(
            title=body.title or "Worker",
            agent_id=body.agent_id,
            host_id=body.host_id,
            workspace=body.workspace,
            kind="sub_agent" if getattr(body, "parent_session_id", None) else "default",
            parent_conversation_id=getattr(body, "parent_session_id", None),
        )

    handler = WorkerDispatchHandler(
        store=queue_store,
        task_store=task_store,
        task_item_store=item_store,
        task_event_store=event_store,
        worker_store=worker_store,
        conversation_store=conversation_store,
        agent_store=agent_store,
        task_role_profile_store=profile_store,
        runner_router=None,
        ensure_runner=ensure_runner,
        session_creator=_mock_session_creator,
        app_state=SimpleNamespace(),
    )
    return {
        "handler": handler,
        "queue_store": queue_store,
        "task_store": task_store,
        "event_store": event_store,
        "item_store": item_store,
        "worker_store": worker_store,
        "conversation_store": conversation_store,
        "agent_store": agent_store,
        "profile_store": profile_store,
        "worker_agent_id": worker_agent_id,
        "task_id": task_id,
        "worker": worker,
        "item": item,
        "manager_conv_id": manager_conv.id,
        "owner": "user-w",
        "ensure_runner": ensure_runner,
    }


@pytest.mark.asyncio
async def test_resolve_target_fresh_slot_is_dispatchable(worker_setup: dict) -> None:
    handler: WorkerDispatchHandler = worker_setup["handler"]
    worker = worker_setup["worker"]
    key = AgentQueueKey(role="worker", owner_user_id=worker_setup["owner"], scope_id=worker.id)
    target = await handler.resolve_target(_queue_item(key, source_id=worker_setup["item"].id))
    assert target.session_id is None  # fresh slot → gate dispatches immediately


@pytest.mark.asyncio
async def test_resolve_target_uses_prior_session_harness(worker_setup: dict) -> None:
    handler: WorkerDispatchHandler = worker_setup["handler"]
    worker_store: SqlAlchemyWorkerStore = worker_setup["worker_store"]
    conversation_store: SqlAlchemyConversationStore = worker_setup["conversation_store"]
    worker = worker_setup["worker"]
    prev_conv = conversation_store.create_conversation(
        kind="sub_agent",
        title="Prev item",
        parent_conversation_id=worker_setup["manager_conv_id"],
        agent_id=worker_setup["worker_agent_id"],
        host_id=_uid("host"),
        workspace="/tmp/prev",
    )
    conversation_store.update_conversation(prev_conv.id, harness_override="claude-native")
    worker_store.update_worker(worker.id, session_id=prev_conv.id)
    key = AgentQueueKey(role="worker", owner_user_id=worker_setup["owner"], scope_id=worker.id)
    target = await handler.resolve_target(_queue_item(key, source_id=worker_setup["item"].id))
    assert target.session_id == prev_conv.id
    assert target.harness == "claude-native"


@pytest.mark.asyncio
async def test_resolve_target_fails_when_worker_missing(worker_setup: dict) -> None:
    handler: WorkerDispatchHandler = worker_setup["handler"]
    key = AgentQueueKey(
        role="worker",
        owner_user_id=worker_setup["owner"],
        scope_id=_uid("ghost_worker"),
    )
    with pytest.raises(DispatchFailed):
        await handler.resolve_target(_queue_item(key, source_id=worker_setup["item"].id))


@pytest.mark.asyncio
async def test_resolve_target_waits_while_worker_initializes() -> None:
    worker = Worker(
        id=_uid("initializing-worker"),
        task_id=_uid("initializing-task"),
        kind="managed",
        state="initializing",
        created_at=now_epoch(),
    )
    worker_store = SimpleNamespace(get_worker=lambda _worker_id: worker)
    session_creator = AsyncMock()
    handler = WorkerDispatchHandler(
        store=SimpleNamespace(),
        task_store=SimpleNamespace(),
        task_item_store=SimpleNamespace(),
        task_event_store=SimpleNamespace(),
        worker_store=worker_store,
        conversation_store=SimpleNamespace(),
        agent_store=SimpleNamespace(),
        task_role_profile_store=SimpleNamespace(),
        runner_router=None,
        session_creator=session_creator,
        app_state=SimpleNamespace(),
    )
    key = AgentQueueKey(role="worker", owner_user_id="user-w", scope_id=worker.id)

    target = await handler.resolve_target(_queue_item(key, source_id=_uid("initializing-item")))

    assert target == DispatchTarget(session_id=None, ready=False)
    session_creator.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_creates_worker_session_and_caches_conversation(
    worker_setup: dict,
) -> None:
    handler: WorkerDispatchHandler = worker_setup["handler"]
    queue_store: SqlAlchemyAgentQueueStore = worker_setup["queue_store"]
    item_store: SqlAlchemyTaskItemStore = worker_setup["item_store"]
    worker = worker_setup["worker"]
    item = worker_setup["item"]
    key = AgentQueueKey(role="worker", owner_user_id=worker_setup["owner"], scope_id=worker.id)
    queue_item = _queue_item(
        key,
        source_id=item.id,
        payload={
            "instructions": item.instructions or "",
            "internal_note": item.internal_note,
            "worker_role_key": worker.role_key,
            "host_id": _uid("host"),
            "workspace": "/tmp/worker",
            "harness": "claude-native",
            "model": "composer-2.5",
        },
    )
    target = DispatchTarget(session_id=None)
    # The dispatcher only delivers items the packager/enqueue path already
    # created a queue row for, so create it here too.
    queue_store.enqueue(
        _uid("q"),
        key,
        "item.dispatch",
        source_ids=[item.id],
        payload=json.dumps({}),
    )
    await handler.deliver(queue_item, target)

    refreshed_item = item_store.get_item(item.id)
    assert refreshed_item is not None
    assert refreshed_item.state == "running"
    execution = worker_setup["event_store"].get_execution_by_agent_queue_item_id(
        queue_item.id,
    )
    assert execution is not None
    assert execution.status == "running"
    queue = queue_store.get_queue(key)
    assert queue is not None and queue.conversation_id is not None
    worker_conv = worker_setup["conversation_store"].get_conversation(queue.conversation_id)
    assert worker_conv is not None
    assert worker_conv.kind == "default"
    assert worker_conv.parent_conversation_id is None
    messages = worker_setup["conversation_store"].list_items(
        queue.conversation_id,
        limit=10,
        order="asc",
    )
    assert [message.response_id for message in messages.data] == [execution.id]
    worker_setup["ensure_runner"].assert_called_once()
    # The runner was ensured for the freshly created worker conversation.
    assert worker_setup["ensure_runner"].call_args.args[0] == queue.conversation_id


@pytest.mark.asyncio
async def test_accept_enqueues_item_dispatch_to_worker_queue(db_uri: str) -> None:
    agent_store = SqlAlchemyAgentStore(db_uri)
    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)
    queue_store = SqlAlchemyAgentQueueStore(db_uri)

    manager_id = generate_agent_id()
    worker_agent_id = generate_agent_id()
    agent_store.create(manager_id, name="manager", bundle_location="test:///b")
    agent_store.create(worker_agent_id, name="worker", bundle_location="test:///b")
    manager_conv = conversation_store.create_conversation(
        title="Manager",
        agent_id=manager_id,
        host_id=_uid("host"),
        workspace="/tmp/mgr",
    )
    task_id = _uid("task_accept")
    task = task_store.create(
        task_id,
        "Accept task",
        "accept goal",
        owner_user_id="user-accept",
        manager_conversation_id=manager_conv.id,
    )
    item = item_store.create_item(
        _uid("inbox_item"),
        task_id,
        "Do work",
        state="pending",
        instructions="Do the work",
    )
    worker = worker_store.create_worker(
        uuid.uuid4().hex,
        task_id,
        kind="managed",
    )
    item = item_store.update_item(item.id, worker_id=worker.id)
    assert item is not None

    updated, execution = await resolve_task_item(
        item=item,
        resolution="accept_item",
        task=task,
        task_store=task_store,
        task_item_store=item_store,
        task_event_store=event_store,
        worker_store=worker_store,
        conversation_store=conversation_store,
        role_profile=_worker_role_profile(worker_agent_id, workspace="/tmp/omnigent-accept"),
        edited_payload={
            "host_id": _uid("host"),
            "workspace": "/tmp/omnigent-accept",
        },
        agent_queue_store=queue_store,
        owner_user_id="user-accept",
    )
    assert updated.state == "queued"
    assert execution is None
    items = queue_store.list_items(
        AgentQueueKey(
            role="worker",
            owner_user_id="user-accept",
            scope_id=updated.worker_id,
        )
    )
    assert len(items) == 1
    assert items[0].kind == "item.dispatch"
    assert items[0].source_ids == [item.id]
    payload = json.loads(items[0].payload)
    assert payload["worker_role_key"] == WORKER_DEFAULT_ROLE_KEY


@pytest.mark.asyncio
async def test_accept_without_queue_store_falls_back_to_sync_dispatch(db_uri: str) -> None:
    agent_store = SqlAlchemyAgentStore(db_uri)
    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)

    manager_id = generate_agent_id()
    worker_agent_id = generate_agent_id()
    agent_store.create(manager_id, name="manager", bundle_location="test:///b")
    agent_store.create(worker_agent_id, name="worker", bundle_location="test:///b")
    manager_conv = conversation_store.create_conversation(
        title="Manager",
        agent_id=manager_id,
        host_id=_uid("host"),
        workspace="/tmp/mgr",
    )
    task_id = _uid("task_legacy")
    task = task_store.create(
        task_id,
        "Legacy task",
        "legacy goal",
        owner_user_id="user-legacy",
        manager_conversation_id=manager_conv.id,
    )
    item = item_store.create_item(
        _uid("legacy_item"),
        task_id,
        "Do work",
        state="pending",
        instructions="Do the work",
    )
    worker = worker_store.create_worker(
        uuid.uuid4().hex,
        task_id,
        kind="managed",
    )
    item = item_store.update_item(item.id, worker_id=worker.id)
    assert item is not None

    async def _mock_session_creator(*, body, request, user_id, **kwargs):
        return conversation_store.create_conversation(
            title=body.title or "Worker",
            agent_id=body.agent_id,
            host_id=body.host_id,
            workspace=body.workspace,
            kind="sub_agent",
            parent_conversation_id=task.manager_conversation_id,
        )

    updated, execution = await resolve_task_item(
        item=item,
        resolution="accept_item",
        task=task,
        task_store=task_store,
        task_item_store=item_store,
        task_event_store=event_store,
        worker_store=worker_store,
        conversation_store=conversation_store,
        role_profile=_worker_role_profile(worker_agent_id, workspace="/tmp/omnigent-legacy"),
        edited_payload={
            "host_id": _uid("host"),
            "workspace": "/tmp/omnigent-legacy",
        },
        session_creator=_mock_session_creator,
        app_state=SimpleNamespace(),
    )
    # No queue store wired → legacy synchronous dispatch path.
    assert execution is not None
    assert updated.state == "running"


def test_resume_endpoint_rearms_halted_queue(db_uri: str) -> None:
    queue_store = SqlAlchemyAgentQueueStore(db_uri)
    key = AgentQueueKey(
        role="worker",
        owner_user_id="user-resume",
        scope_id=_uid("slot"),
    )
    queue_store.enqueue(_uid("q"), key, "item.dispatch", source_ids=[_uid("ti")])
    queue_store.fail_dispatch(_uid("q"), key, error="boom", now=now_epoch())
    halted = queue_store.get_queue(key)
    assert halted is not None and halted.state == "halted"

    app = FastAPI()
    app.include_router(create_agent_queues_router(queue_store), prefix="/v1")
    client = TestClient(app)
    response = client.post(
        "/v1/agent-queues/worker/resume",
        json={"owner_user_id": key.owner_user_id, "scope_id": key.scope_id},
    )
    assert response.status_code == 200
    resumed = queue_store.get_queue(key)
    assert resumed is not None and resumed.state == "active"


@pytest.mark.asyncio
async def test_shared_worker_lane_carries_other_task_items(worker_setup: dict) -> None:
    """A worker lane may serve items from a different task of the same owner."""
    task_store = worker_setup["task_store"]
    item_store = worker_setup["item_store"]
    worker = worker_setup["worker"]

    other_task_id = _uid("task_other")
    task_store.create(
        other_task_id,
        "Other task",
        "other goal",
        owner_user_id=worker_setup["owner"],
        manager_conversation_id=worker_setup["manager_conv_id"],
    )
    other_item = item_store.create_item(
        _uid("item_other"),
        other_task_id,
        "Work from another task",
        state="queued",
        instructions="Do other work",
        worker_id=worker.id,  # shared lane
    )
    key = AgentQueueKey(role="worker", owner_user_id=worker_setup["owner"], scope_id=worker.id)
    worker_setup["queue_store"].enqueue(
        _uid("queue_other"), key, kind="item.dispatch", source_ids=[other_item.id], payload="{}"
    )
    items = worker_setup["queue_store"].list_items(key)
    assert any(other_item.id in (i.source_ids or []) for i in items)


@pytest.mark.asyncio
async def test_completion_resolves_task_from_execution(worker_setup: dict) -> None:
    """Completion maps to execution.task_id, so a shared lane finishes the right task."""
    from omnigent.agent_tasks.completion import (
        TaskCompletionContext,
        configure_task_completion,
        notify_worker_session_status,
    )

    task_store = worker_setup["task_store"]
    item_store = worker_setup["item_store"]
    event_store = worker_setup["event_store"]
    worker = worker_setup["worker"]
    worker_store = worker_setup["worker_store"]
    conversation_store = worker_setup["conversation_store"]
    worker_conv_id = _uid("worker_conv")
    worker_store.update_worker(worker.id, target_id=worker_conv_id, state="busy")

    other_task_id = _uid("task_comp")
    task_store.create(
        other_task_id,
        "Completion task",
        "comp goal",
        owner_user_id=worker_setup["owner"],
        manager_conversation_id=worker_setup["manager_conv_id"],
    )
    other_item = item_store.create_item(
        _uid("item_comp"),
        other_task_id,
        "Work from another task",
        state="running",
        instructions="Do work",
        worker_id=worker.id,
    )
    execution = event_store.create_execution(
        _uid("exec_shared"),
        other_item.id,
        other_task_id,  # execution names the real task, not worker.task_id
        status="running",
        conversation_id=worker_conv_id,
    )
    configure_task_completion(
        TaskCompletionContext(
            task_store=task_store,
            task_event_store=event_store,
            task_item_store=item_store,
            conversation_store=conversation_store,
            worker_store=worker_store,
            agent_queue_store=None,
            runner_router=None,
        )
    )
    await notify_worker_session_status(execution.conversation_id, "idle", output="done")
    updated = item_store.get_item(other_item.id)
    assert updated.state == "done"
    # The completion event targets the item's task, not the worker's home
    # task — this is what pins execution.task_id over worker.task_id.
    events = event_store.list_events(state="routed", task_id=other_task_id)
    assert len(events) == 1
    assert events[0].task_id == other_task_id
    home_events = event_store.list_events(state="routed", task_id=worker_setup["task_id"])
    assert home_events == []
