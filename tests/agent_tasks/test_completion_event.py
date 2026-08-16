"""Tests for the worker-completion event emission.

The completion hook emits a pre-routed ``worker.execution.finished`` event.
When an ``agent_queue_store`` is configured, it also directly enqueues a
manager notice and marks the event ``reconciled`` so the packager skips it.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid

import pytest

from omnigent.agent_tasks.completion import (
    TaskCompletionContext,
    configure_task_completion,
    notify_worker_session_status,
)
from omnigent.agent_tasks.event_gc import EventGcConfig, run_event_gc
from omnigent.agent_tasks.event_types import WORKER_EXECUTION_FINISHED_EVENT_TYPE
from omnigent.agent_tasks.notices import _format_worker_notice
from omnigent.agent_tasks.role_keys import WORKER_DEFAULT_ROLE_KEY
from omnigent.db.utils import generate_agent_id
from omnigent.entities.agent_queue import AgentQueueKey
from omnigent.stores.agent_queue_store.sqlalchemy_store import SqlAlchemyAgentQueueStore
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


def _build_stores(db_uri: str) -> dict:
    return {
        "agent_store": SqlAlchemyAgentStore(db_uri),
        "task_store": SqlAlchemyTaskStore(db_uri),
        "event_store": SqlAlchemyTaskEventStore(db_uri),
        "item_store": SqlAlchemyTaskItemStore(db_uri),
        "conversation_store": SqlAlchemyConversationStore(db_uri),
        "worker_store": SqlAlchemyWorkerStore(db_uri),
        "queue_store": SqlAlchemyAgentQueueStore(db_uri),
    }


def _seed_task(stores: dict, *, task_seed: str, owner: str | None) -> dict:
    agent_store = stores["agent_store"]
    task_store = stores["task_store"]
    item_store = stores["item_store"]
    conversation_store = stores["conversation_store"]
    worker_store = stores["worker_store"]

    manager_agent_id = generate_agent_id()
    agent_store.create(
        manager_agent_id, name="task-manager-agent", bundle_location="test:///bundle"
    )
    worker_agent_id = generate_agent_id()
    agent_store.create(worker_agent_id, name="task-worker-agent", bundle_location="test:///bundle")

    task_id = _uid(task_seed)
    manager_conv = conversation_store.create_conversation(
        title="Manager",
        agent_id=manager_agent_id,
        host_id=_uid("host_mgr_" + task_seed),
        workspace="/tmp/mgr",
    )
    task_store.create(
        task_id,
        "Completion task",
        "complete the task",
        owner_user_id=owner,
        manager_conversation_id=manager_conv.id,
    )
    task_item_id = _uid("item_" + task_seed)
    item_store.create_item(
        task_item_id,
        task_id,
        "Fix the login flow",
        state="running",
        instructions="Run the auth tests and report failures.",
    )
    worker = worker_store.create_worker(
        _uid("worker_" + task_seed),
        task_id,
        role_key=WORKER_DEFAULT_ROLE_KEY,
    )
    worker_conv = conversation_store.create_conversation(
        kind="sub_agent",
        title="Worker",
        parent_conversation_id=manager_conv.id,
        agent_id=worker_agent_id,
        host_id=_uid("host_worker_" + task_seed),
        workspace="/tmp/worker",
    )
    worker_store.update_worker(worker.id, session_id=worker_conv.id)
    execution = stores["event_store"].create_execution(
        _uid("exec_" + task_seed),
        task_item_id,
        task_id,
        status="running",
        conversation_id=worker_conv.id,
    )
    return {
        "task_id": task_id,
        "owner": owner or "__anonymous__",
        "task_item_id": task_item_id,
        "execution_id": execution.id,
        "worker_conv_id": worker_conv.id,
    }


@pytest.fixture
def completion_setup(db_uri: str) -> dict:
    stores = _build_stores(db_uri)
    seeded = _seed_task(stores, task_seed="comp", owner="user-comp")
    configure_task_completion(
        TaskCompletionContext(
            task_store=stores["task_store"],
            task_event_store=stores["event_store"],
            task_item_store=stores["item_store"],
            conversation_store=stores["conversation_store"],
            worker_store=stores["worker_store"],
            runner_router=None,
        )
    )
    return {**stores, **seeded}


@pytest.fixture
def completion_setup_with_queue(db_uri: str) -> dict:
    stores = _build_stores(db_uri)
    seeded = _seed_task(stores, task_seed="comp_q", owner="user-comp")
    configure_task_completion(
        TaskCompletionContext(
            task_store=stores["task_store"],
            task_event_store=stores["event_store"],
            task_item_store=stores["item_store"],
            conversation_store=stores["conversation_store"],
            worker_store=stores["worker_store"],
            agent_queue_store=stores["queue_store"],
            runner_router=None,
        )
    )
    return {**stores, **seeded}


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

    execution = event_store.get_execution(completion_setup["execution_id"])
    assert execution is not None
    assert execution.status == "succeeded"
    assert execution.result_summary == "Root cause was a stale credential."
    item = completion_setup["item_store"].get_item(completion_setup["task_item_id"])
    assert item is not None
    assert item.state == "done"

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
    assert payload["instructions"] == "Run the auth tests and report failures."
    assert payload["output"] == "Root cause was a stale credential."


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
    item = completion_setup["item_store"].get_item(completion_setup["task_item_id"])
    assert item is not None
    assert item.state == "queued"

    routed = event_store.list_events(state="routed", task_id=completion_setup["task_id"])
    finished = [e for e in routed if e.event_type == WORKER_EXECUTION_FINISHED_EVENT_TYPE]
    assert len(finished) == 1
    payload = json.loads(finished[0].payload)
    assert payload["status"] == "failed"
    assert payload["error"] == "Worker crashed mid-run."
    assert payload["output"] == "Worker crashed mid-run."


@pytest.mark.asyncio
async def test_with_queue_enqueues_notice_and_reconciles(
    completion_setup_with_queue: dict,
) -> None:
    event_store: SqlAlchemyTaskEventStore = completion_setup_with_queue["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = completion_setup_with_queue["queue_store"]

    handled = await notify_worker_session_status(
        completion_setup_with_queue["worker_conv_id"],
        "idle",
        output="All tests passed.",
    )
    assert handled is True

    routed = event_store.list_events(
        state="routed", task_id=completion_setup_with_queue["task_id"]
    )
    assert len(routed) == 0
    reconciled = event_store.list_events(
        state="reconciled", task_id=completion_setup_with_queue["task_id"]
    )
    assert len(reconciled) == 1
    event = reconciled[0]
    assert event.processed_at is not None

    key = AgentQueueKey(
        role="manager",
        owner_user_id=completion_setup_with_queue["owner"],
        scope_id=completion_setup_with_queue["task_id"],
    )
    items = queue_store.list_items(key)
    notices = [i for i in items if i.kind == "notice"]
    assert len(notices) == 1
    notice = notices[0]
    assert event.id in (notice.source_ids or [])
    notice_text = notice.payload
    assert "Fix the login flow" in notice_text
    assert "All tests passed." in notice_text


@pytest.mark.asyncio
async def test_orphan_owner_event_uses_anonymous_owner(db_uri: str) -> None:
    stores = _build_stores(db_uri)
    seeded = _seed_task(stores, task_seed="anon", owner=None)
    configure_task_completion(
        TaskCompletionContext(
            task_store=stores["task_store"],
            task_event_store=stores["event_store"],
            task_item_store=stores["item_store"],
            conversation_store=stores["conversation_store"],
            worker_store=stores["worker_store"],
            runner_router=None,
        )
    )
    await notify_worker_session_status(seeded["worker_conv_id"], "idle", output="ok")
    routed = stores["event_store"].list_events(state="routed", task_id=seeded["task_id"])
    finished = [e for e in routed if e.event_type == WORKER_EXECUTION_FINISHED_EVENT_TYPE]
    assert len(finished) == 1
    assert finished[0].owner_user_id == "__anonymous__"


# ── Notice formatting ────────────────────────────────────────────────


def _make_event(payload: dict, title: str = "Worker execution finished for item X") -> object:
    class _E:
        def __init__(self) -> None:
            self.event_type = WORKER_EXECUTION_FINISHED_EVENT_TYPE
            self.title = title
            self.payload = json.dumps(payload)

    return _E()


def test_format_worker_notice_includes_output_and_instructions() -> None:
    event = _make_event(
        {
            "status": "succeeded",
            "item_title": "Fix the login flow",
            "instructions": "Run the auth tests and report failures.",
            "result_summary": "All tests passed.",
            "output": "test_auth.py ......... 9 passed",
        }
    )
    text = _format_worker_notice(event)
    assert "Worker finished a turn (status: succeeded)" in text
    assert "Fix the login flow" in text
    assert "Run the auth tests and report failures." in text
    assert "All tests passed." in text
    assert "test_auth.py ......... 9 passed" in text
    assert "suggest a new taskItem" in text


def test_format_worker_notice_failed_status_uses_error() -> None:
    event = _make_event(
        {
            "status": "failed",
            "item_title": "Fix the login flow",
            "instructions": None,
            "error": "Worker crashed mid-run.",
            "output": "Traceback (most recent call last): ...",
        }
    )
    text = _format_worker_notice(event)
    assert "status: failed" in text
    assert "Worker crashed mid-run." in text
    assert "Traceback (most recent call last): ..." in text


# ── GC ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_purge_old_events_deletes_reconciled_only(db_uri: str) -> None:
    stores = _build_stores(db_uri)
    event_store: SqlAlchemyTaskEventStore = stores["event_store"]
    seeded = _seed_task(stores, task_seed="gc_evt", owner="user-gc")
    configure_task_completion(
        TaskCompletionContext(
            task_store=stores["task_store"],
            task_event_store=event_store,
            task_item_store=stores["item_store"],
            conversation_store=stores["conversation_store"],
            worker_store=stores["worker_store"],
            runner_router=None,
        )
    )
    await notify_worker_session_status(seeded["worker_conv_id"], "idle", output="ok")
    routed = event_store.list_events(state="routed", task_id=seeded["task_id"])
    assert len(routed) == 1

    n = event_store.purge_old_events(before_ts=int(time.time()) + 10_000, states=["reconciled"])
    assert n == 0
    assert len(event_store.list_events(state="routed", task_id=seeded["task_id"])) == 1

    event_store.update_event(routed[0].id, state="reconciled", processed_at=int(time.time()))
    n = event_store.purge_old_events(before_ts=int(time.time()) + 10_000, states=["reconciled"])
    assert n == 1
    assert len(event_store.list_events(state="reconciled", task_id=seeded["task_id"])) == 0


@pytest.mark.asyncio
async def test_purge_old_items_keeps_queued_notice(db_uri: str) -> None:
    stores = _build_stores(db_uri)
    queue_store: SqlAlchemyAgentQueueStore = stores["queue_store"]
    seeded = _seed_task(stores, task_seed="gc_item", owner="user-gc")
    configure_task_completion(
        TaskCompletionContext(
            task_store=stores["task_store"],
            task_event_store=stores["event_store"],
            task_item_store=stores["item_store"],
            conversation_store=stores["conversation_store"],
            worker_store=stores["worker_store"],
            agent_queue_store=queue_store,
            runner_router=None,
        )
    )
    await notify_worker_session_status(seeded["worker_conv_id"], "idle", output="ok")

    key = AgentQueueKey(
        role="manager",
        owner_user_id=seeded["owner"],
        scope_id=seeded["task_id"],
    )
    items = queue_store.list_items(key)
    notices = [i for i in items if i.kind == "notice"]
    assert len(notices) == 1
    n = queue_store.purge_old_items(before_ts=int(time.time()) + 10_000, states=["done"])
    assert n == 0
    assert len(queue_store.list_items(key)) == 1


@pytest.mark.asyncio
async def test_run_event_gc_purges_old_reconciled(
    db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = _build_stores(db_uri)
    event_store: SqlAlchemyTaskEventStore = stores["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = stores["queue_store"]
    seeded = _seed_task(stores, task_seed="gc_run", owner="user-gc")
    configure_task_completion(
        TaskCompletionContext(
            task_store=stores["task_store"],
            task_event_store=event_store,
            task_item_store=stores["item_store"],
            conversation_store=stores["conversation_store"],
            worker_store=stores["worker_store"],
            agent_queue_store=queue_store,
            runner_router=None,
        )
    )
    await notify_worker_session_status(seeded["worker_conv_id"], "idle", output="ok")

    reconciled = event_store.list_events(state="reconciled", task_id=seeded["task_id"])
    assert len(reconciled) == 1

    config = EventGcConfig(
        interval_s=0.01,
        reconciled_retention_s=0.0,
        stale_routed_retention_s=0.0,
        queue_retention_s=10_000_000.0,
    )

    call_count = {"n": 0}

    async def _sleep_then_cancel(seconds: float) -> None:
        call_count["n"] += 1
        if call_count["n"] > 1:
            raise asyncio.CancelledError

    # Advance the GC's clock so the just-created event falls before the cutoff.
    real_now = time.time()
    monkeypatch.setattr(
        "omnigent.agent_tasks.event_gc._now",
        lambda: int(real_now) + 10_000,
    )
    monkeypatch.setattr(
        "omnigent.agent_tasks.event_gc._sleep",
        _sleep_then_cancel,
    )

    with pytest.raises(asyncio.CancelledError):
        await run_event_gc(event_store, queue_store, config=config)

    assert len(event_store.list_events(state="reconciled", task_id=seeded["task_id"])) == 0
