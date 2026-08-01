"""Tests for the worker-completion event emission.

The completion hook no longer wakes the manager directly. Instead it emits a
pre-routed ``worker.execution.finished`` event that the manager packager polls.
"""

from __future__ import annotations

import json
import uuid

import pytest

from omnigent.agent_tasks.completion import (
    TaskCompletionContext,
    configure_task_completion,
    notify_worker_session_status,
)
from omnigent.agent_tasks.event_types import WORKER_EXECUTION_FINISHED_EVENT_TYPE
from omnigent.db.utils import generate_agent_id
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.fixture
def completion_setup(db_uri: str) -> dict:
    agent_store = SqlAlchemyAgentStore(db_uri)
    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)

    manager_agent_id = generate_agent_id()
    agent_store.create(
        manager_agent_id, name="task-manager-agent", bundle_location="test:///bundle"
    )
    worker_agent_id = generate_agent_id()
    agent_store.create(worker_agent_id, name="task-worker-agent", bundle_location="test:///bundle")

    task_id = _uid("task_comp")
    owner = "user-comp"
    manager_conv = conversation_store.create_conversation(
        title="Manager",
        agent_id=manager_agent_id,
        host_id=_uid("host_mgr"),
        workspace="/tmp/mgr",
    )
    task_store.create(
        task_id,
        "Completion task",
        agent_profile_id=manager_agent_id,
        owner_user_id=owner,
        manager_conversation_id=manager_conv.id,
    )

    task_item_id = _uid("item_comp")
    item_store.create_item(task_item_id, task_id, "Fix the login flow", state="running")

    worker = worker_store.create_worker(_uid("worker_comp"), task_id, worker_agent_id)
    worker_conv = conversation_store.create_conversation(
        kind="sub_agent",
        title="Worker",
        parent_conversation_id=manager_conv.id,
        agent_id=worker_agent_id,
        host_id=_uid("host_worker"),
        workspace="/tmp/worker",
    )
    worker_store.update_worker(worker.id, session_id=worker_conv.id)
    execution = event_store.create_execution(
        _uid("exec_comp"),
        task_item_id,
        task_id,
        status="running",
        conversation_id=worker_conv.id,
    )

    configure_task_completion(
        TaskCompletionContext(
            task_store=task_store,
            task_event_store=event_store,
            task_item_store=item_store,
            conversation_store=conversation_store,
            worker_store=worker_store,
            runner_router=None,
        )
    )
    return {
        "event_store": event_store,
        "item_store": item_store,
        "task_store": task_store,
        "task_id": task_id,
        "owner": owner,
        "task_item_id": task_item_id,
        "execution_id": execution.id,
        "worker_conv_id": worker_conv.id,
    }


@pytest.fixture(autouse=True)
def _clear_completion() -> None:
    yield
    configure_task_completion(None)


@pytest.mark.asyncio
async def test_idle_emits_routed_worker_finished_event(completion_setup: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = completion_setup["event_store"]
    handled = await notify_worker_session_status(
        completion_setup["worker_conv_id"],
        "idle",
        output="Root cause was a stale credential.",
    )
    assert handled is True

    # Execution + item reach their terminal state.
    execution = event_store.get_execution(completion_setup["execution_id"])
    assert execution is not None
    assert execution.status == "succeeded"
    assert execution.result_summary == "Root cause was a stale credential."
    item = completion_setup["item_store"].get_item(completion_setup["task_item_id"])
    assert item is not None
    assert item.state == "done"

    # A pre-routed worker.execution.finished event is emitted for the manager packager.
    routed = event_store.list_events(state="routed", task_id=completion_setup["task_id"])
    finished = [e for e in routed if e.event_type == WORKER_EXECUTION_FINISHED_EVENT_TYPE]
    assert len(finished) == 1
    event = finished[0]
    assert event.state == "routed"
    assert event.task_id == completion_setup["task_id"]
    assert event.owner_user_id == completion_setup["owner"]
    assert event.source == "worker"
    assert event.source_key == completion_setup["execution_id"]
    assert event.routed_at is not None
    payload = json.loads(event.payload)
    assert payload["execution_id"] == completion_setup["execution_id"]
    assert payload["status"] == "succeeded"
    assert payload["item_title"] == "Fix the login flow"
    assert payload["result_summary"] == "Root cause was a stale credential."


@pytest.mark.asyncio
async def test_failed_emits_routed_worker_finished_event(completion_setup: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = completion_setup["event_store"]
    handled = await notify_worker_session_status(
        completion_setup["worker_conv_id"],
        "failed",
        output="Worker crashed mid-run.",
    )
    assert handled is True

    execution = event_store.get_execution(completion_setup["execution_id"])
    assert execution is not None
    assert execution.status == "failed"
    assert execution.error == "Worker crashed mid-run."
    # A failed execution returns the item to queued, not done.
    item = completion_setup["item_store"].get_item(completion_setup["task_item_id"])
    assert item is not None
    assert item.state == "queued"

    routed = event_store.list_events(state="routed", task_id=completion_setup["task_id"])
    finished = [e for e in routed if e.event_type == WORKER_EXECUTION_FINISHED_EVENT_TYPE]
    assert len(finished) == 1
    payload = json.loads(finished[0].payload)
    assert payload["status"] == "failed"
    assert payload["error"] == "Worker crashed mid-run."


@pytest.mark.asyncio
async def test_orphan_owner_event_uses_anonymous_owner(db_uri: str) -> None:
    """A task with no owner attributes the finished event to __anonymous__."""
    agent_store = SqlAlchemyAgentStore(db_uri)
    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    item_store = SqlAlchemyTaskItemStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)

    manager_agent_id = generate_agent_id()
    agent_store.create(
        manager_agent_id, name="task-manager-agent", bundle_location="test:///bundle"
    )
    worker_agent_id = generate_agent_id()
    agent_store.create(worker_agent_id, name="task-worker-agent", bundle_location="test:///bundle")
    task_id = _uid("task_anon")
    manager_conv = conversation_store.create_conversation(
        title="Manager", agent_id=manager_agent_id, host_id=_uid("h"), workspace="/tmp"
    )
    task_store.create(
        task_id,
        "Anon task",
        agent_profile_id=manager_agent_id,
        manager_conversation_id=manager_conv.id,
    )
    task_item_id = _uid("item_anon")
    item_store.create_item(task_item_id, task_id, "Do thing", state="running")
    worker = worker_store.create_worker(_uid("worker_anon"), task_id, worker_agent_id)
    worker_conv = conversation_store.create_conversation(
        kind="sub_agent",
        title="Worker",
        parent_conversation_id=manager_conv.id,
        agent_id=worker_agent_id,
        host_id=_uid("hw"),
        workspace="/tmp",
    )
    worker_store.update_worker(worker.id, session_id=worker_conv.id)
    event_store.create_execution(
        _uid("exec_anon"),
        task_item_id,
        task_id,
        status="running",
        conversation_id=worker_conv.id,
    )
    configure_task_completion(
        TaskCompletionContext(
            task_store=task_store,
            task_event_store=event_store,
            task_item_store=item_store,
            conversation_store=conversation_store,
            worker_store=worker_store,
            runner_router=None,
        )
    )
    await notify_worker_session_status(worker_conv.id, "idle", output="ok")
    routed = event_store.list_events(state="routed", task_id=task_id)
    finished = [e for e in routed if e.event_type == WORKER_EXECUTION_FINISHED_EVENT_TYPE]
    assert len(finished) == 1
    assert finished[0].owner_user_id == "__anonymous__"
