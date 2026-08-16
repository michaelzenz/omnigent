"""Tests for the manager packager (poll-based batching of routed events)."""

from __future__ import annotations

import json
import uuid

import pytest

from omnigent.agent_tasks.agent_builtins import TASK_MANAGER_ROLE
from omnigent.agent_tasks.event_types import WORKER_EXECUTION_FINISHED_EVENT_TYPE
from omnigent.agent_tasks.queue.packagers import (
    DEFAULT_PACKAGER_AGE_THRESHOLD_S,
    DEFAULT_PACKAGER_POLL_INTERVAL_S,
    ManagerPackager,
    _StatusReader,
)
from omnigent.db.utils import generate_agent_id
from omnigent.entities import AgentQueueKey
from omnigent.stores.agent_queue_store.sqlalchemy_store import SqlAlchemyAgentQueueStore
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


class _StaticStatusReader(_StatusReader):
    """Reports a fixed status for every session."""

    def __init__(self, status: str | None = "idle") -> None:
        self.status = status

    def status_for(self, session_id: str) -> str | None:
        return self.status


@pytest.fixture
def manager_setup(db_uri: str) -> dict:
    agent_store = SqlAlchemyAgentStore(db_uri)
    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)
    queue_store = SqlAlchemyAgentQueueStore(db_uri)

    manager_agent_id = generate_agent_id()
    agent_store.create(
        manager_agent_id, name="task-manager-agent", bundle_location="test:///bundle"
    )
    manager_conv = conversation_store.create_conversation(
        title="Manager",
        agent_id=manager_agent_id,
        host_id=_uid("host_mgr"),
        workspace="/tmp/mgr",
    )

    task_id = _uid("task_a")
    owner = "user-1"
    task_store.create(
        task_id,
        "Manager task",
        "manager goal",
        owner_user_id=owner,
        manager_conversation_id=manager_conv.id,
    )

    status_reader = _StaticStatusReader("idle")
    packager = ManagerPackager(
        store=queue_store,
        task_event_store=event_store,
        task_store=task_store,
        status_reader=status_reader,
        # Negative threshold so freshly-created events qualify immediately
        # when idle (age 0 > -1). Tests that need "wait because young" raise it.
        age_threshold_s=-1.0,
        batch_size=10,
    )
    return {
        "agent_store": agent_store,
        "task_store": task_store,
        "event_store": event_store,
        "conversation_store": conversation_store,
        "queue_store": queue_store,
        "task_id": task_id,
        "owner": owner,
        "manager_conv_id": manager_conv.id,
        "packager": packager,
        "status_reader": status_reader,
    }


def _key(owner: str, task_id: str) -> AgentQueueKey:
    return AgentQueueKey(
        role=TASK_MANAGER_ROLE,
        owner_user_id=owner,
        scope_id=task_id,
    )


def _routed_event(
    setup: dict,
    *,
    seed: str,
    event_type: str = "build.finished",
    title: str = "Build broke",
    task_id: str | None = None,
    payload: str | None = None,
) -> str:
    event_store: SqlAlchemyTaskEventStore = setup["event_store"]
    event_id = _uid(seed)
    event_store.create_event(
        event_id,
        event_type,
        title,
        task_id=task_id or setup["task_id"],
        state="routed",
        payload=payload,
        owner_user_id=setup["owner"],
    )
    return event_id


@pytest.mark.asyncio
async def test_full_batch_sends_regardless_of_agent_state(manager_setup: dict) -> None:
    queue_store: SqlAlchemyAgentQueueStore = manager_setup["queue_store"]
    packager: ManagerPackager = manager_setup["packager"]
    manager_setup["status_reader"].status = "running"  # manager busy
    packager._batch_size = 3

    for i in range(3):
        _routed_event(manager_setup, seed=f"evt{i}")
    await packager.scan_once()

    assert len(queue_store.list_items(_key(manager_setup["owner"], manager_setup["task_id"]))) == 1


@pytest.mark.asyncio
async def test_partial_batch_waits_when_agent_busy(manager_setup: dict) -> None:
    queue_store: SqlAlchemyAgentQueueStore = manager_setup["queue_store"]
    packager: ManagerPackager = manager_setup["packager"]
    manager_setup["status_reader"].status = "running"
    packager._age_threshold_s = -1.0  # age floor would otherwise force a send

    _routed_event(manager_setup, seed="evt")
    await packager.scan_once()

    assert queue_store.list_items(_key(manager_setup["owner"], manager_setup["task_id"])) == []


@pytest.mark.asyncio
async def test_partial_batch_sends_when_idle_and_age_exceeded(manager_setup: dict) -> None:
    queue_store: SqlAlchemyAgentQueueStore = manager_setup["queue_store"]
    packager: ManagerPackager = manager_setup["packager"]
    manager_setup["status_reader"].status = "idle"
    packager._age_threshold_s = -1.0  # oldest age > 0 immediately

    _routed_event(manager_setup, seed="evt")
    await packager.scan_once()

    items = queue_store.list_items(_key(manager_setup["owner"], manager_setup["task_id"]))
    assert len(items) == 1
    assert "[System: 1 event(s) routed to this task" in items[0].payload


@pytest.mark.asyncio
async def test_partial_batch_waits_when_idle_but_young(manager_setup: dict) -> None:
    queue_store: SqlAlchemyAgentQueueStore = manager_setup["queue_store"]
    packager: ManagerPackager = manager_setup["packager"]
    manager_setup["status_reader"].status = "idle"
    packager._age_threshold_s = 3600  # far above any real age

    _routed_event(manager_setup, seed="evt")
    await packager.scan_once()

    assert queue_store.list_items(_key(manager_setup["owner"], manager_setup["task_id"])) == []


@pytest.mark.asyncio
async def test_claimed_events_are_not_repackaged(manager_setup: dict) -> None:
    queue_store: SqlAlchemyAgentQueueStore = manager_setup["queue_store"]
    packager: ManagerPackager = manager_setup["packager"]
    manager_setup["status_reader"].status = "idle"
    packager._age_threshold_s = -1.0

    _routed_event(manager_setup, seed="evt")
    await packager.scan_once()  # packages it
    await packager.scan_once()  # should not duplicate

    assert len(queue_store.list_items(_key(manager_setup["owner"], manager_setup["task_id"]))) == 1


@pytest.mark.asyncio
async def test_reconciled_events_are_filtered(manager_setup: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = manager_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = manager_setup["queue_store"]
    packager: ManagerPackager = manager_setup["packager"]
    manager_setup["status_reader"].status = "idle"
    packager._age_threshold_s = -1.0

    event_id = _routed_event(manager_setup, seed="reconciled")
    event_store.update_event(event_id, state="reconciled")
    await packager.scan_once()

    assert queue_store.list_items(_key(manager_setup["owner"], manager_setup["task_id"])) == []


@pytest.mark.asyncio
async def test_no_manager_conversation_holds_events(manager_setup: dict) -> None:
    """A task with no manager session yet keeps its events routed, not queued."""
    task_store: SqlAlchemyTaskStore = manager_setup["task_store"]
    queue_store: SqlAlchemyAgentQueueStore = manager_setup["queue_store"]
    packager: ManagerPackager = manager_setup["packager"]
    manager_setup["status_reader"].status = "idle"
    packager._age_threshold_s = -1.0

    # A second task with no manager_conversation_id.
    orphan_task_id = _uid("task_orphan")
    task_store.create(
        orphan_task_id,
        "Orphan task",
        "orphan goal",
        owner_user_id=manager_setup["owner"],
    )
    _routed_event(manager_setup, seed="orphan_evt", task_id=orphan_task_id)
    await packager.scan_once()

    assert queue_store.list_items(_key(manager_setup["owner"], orphan_task_id)) == []


@pytest.mark.asyncio
async def test_worker_execution_finished_event_is_packaged(manager_setup: dict) -> None:
    queue_store: SqlAlchemyAgentQueueStore = manager_setup["queue_store"]
    packager: ManagerPackager = manager_setup["packager"]
    manager_setup["status_reader"].status = "idle"
    packager._age_threshold_s = -1.0

    payload = json.dumps(
        {
            "execution_id": _uid("exec"),
            "status": "succeeded",
            "task_item_id": _uid("item"),
            "item_title": "Fix login",
            "result_summary": "Rotated the credential.",
            "error": None,
        }
    )
    _routed_event(
        manager_setup,
        seed="exec_finished",
        event_type=WORKER_EXECUTION_FINISHED_EVENT_TYPE,
        title="Worker execution succeeded for item 'Fix login'",
        payload=payload,
    )
    await packager.scan_once()

    items = queue_store.list_items(_key(manager_setup["owner"], manager_setup["task_id"]))
    assert len(items) == 1
    assert "worker.execution.finished" in items[0].payload
    assert "Fix login" in items[0].payload
    assert "Rotated the credential." in items[0].payload


@pytest.mark.asyncio
async def test_events_grouped_by_task_id(manager_setup: dict) -> None:
    task_store: SqlAlchemyTaskStore = manager_setup["task_store"]
    queue_store: SqlAlchemyAgentQueueStore = manager_setup["queue_store"]
    packager: ManagerPackager = manager_setup["packager"]
    manager_setup["status_reader"].status = "idle"
    packager._age_threshold_s = -1.0

    # A second task sharing the owner, with its own manager conversation.
    second_task_id = _uid("task_b")
    second_conv = manager_setup["conversation_store"].create_conversation(
        title="Manager B",
        agent_id=manager_setup["agent_store"].get_by_name("task-manager-agent").id,
        host_id=_uid("host_mgr_b"),
        workspace="/tmp/mgr_b",
    )
    task_store.create(
        second_task_id,
        "Second task",
        "second goal",
        owner_user_id=manager_setup["owner"],
        manager_conversation_id=second_conv.id,
    )

    # Two events on task A, one on task B.
    _routed_event(manager_setup, seed="a1")
    _routed_event(manager_setup, seed="a2")
    _routed_event(manager_setup, seed="b1", task_id=second_task_id)
    await packager.scan_once()

    a_items = queue_store.list_items(_key(manager_setup["owner"], manager_setup["task_id"]))
    b_items = queue_store.list_items(_key(manager_setup["owner"], second_task_id))
    assert len(a_items) == 1
    assert len(b_items) == 1
    # The task-A notice bundles both of its events.
    assert "[System: 2 event(s) routed to this task" in a_items[0].payload


def test_defaults_are_configurable_constants() -> None:
    assert DEFAULT_PACKAGER_POLL_INTERVAL_S == 5.0
    assert DEFAULT_PACKAGER_AGE_THRESHOLD_S == 15
