"""Tests for the manager-queue re-key migration."""

from __future__ import annotations

import uuid

import pytest

from omnigent.agent_tasks.agent_builtins import TASK_MANAGER_ROLE
from omnigent.agent_tasks.queue.rekey_migration import rekey_manager_queues
from omnigent.db.utils import generate_agent_id, now_epoch
from omnigent.entities import AgentQueueKey
from omnigent.stores.agent_queue_store.sqlalchemy_store import SqlAlchemyAgentQueueStore
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.fixture
def rekey_setup(db_uri: str) -> dict:
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
    task_id = _uid("task_rekey")
    owner = "user-1"
    task_store.create(
        task_id,
        "Rekey task",
        "rekey goal",
        owner_user_id=owner,
        manager_conversation_id=manager_conv.id,
    )
    return {
        "task_store": task_store,
        "event_store": event_store,
        "queue_store": queue_store,
        "task_id": task_id,
        "owner": owner,
        "manager_conv_id": manager_conv.id,
    }


def _legacy_key(owner: str, task_id: str) -> AgentQueueKey:
    """A pre-v2 manager queue key: scoped by task id."""
    return AgentQueueKey(role=TASK_MANAGER_ROLE, owner_user_id=owner, scope_id=task_id)


def _event(setup: dict, *, seed: str, state: str) -> str:
    event_store: SqlAlchemyTaskEventStore = setup["event_store"]
    event_id = _uid(seed)
    event_store.create_event(
        event_id,
        "build.finished",
        f"event {seed}",
        task_id=setup["task_id"],
        manager_conversation_id=setup["manager_conv_id"],
        state=state,
        owner_user_id=setup["owner"],
    )
    return event_id


def test_open_items_are_canceled_and_routed_events_repackage(rekey_setup: dict) -> None:
    queue_store: SqlAlchemyAgentQueueStore = rekey_setup["queue_store"]
    event_store: SqlAlchemyTaskEventStore = rekey_setup["event_store"]

    routed_event = _event(rekey_setup, seed="evt_routed", state="routed")
    reconciled_event = _event(rekey_setup, seed="evt_reconciled", state="reconciled")
    legacy_key = _legacy_key(rekey_setup["owner"], rekey_setup["task_id"])
    queue_store.enqueue(
        _uid("item_queued"),
        legacy_key,
        "notice",
        source_ids=[routed_event, reconciled_event],
        payload="notice",
    )
    queue_store.enqueue(
        _uid("item_parked"),
        legacy_key,
        "notice",
        source_ids=[_event(rekey_setup, seed="evt_parked", state="routed")],
        payload="notice2",
    )
    queue_store.fail_dispatch(
        _uid("item_parked"),
        legacy_key,
        error="boom",
        now=now_epoch(),
        retryable=False,
        max_retries=0,
        backoff_s=0,
    )

    result = rekey_manager_queues(
        agent_queue_store=queue_store,
        task_event_store=event_store,
    )

    assert result["items_canceled"] == 2
    assert result["events_requeued"] == 2  # the two still-routed events
    assert result["items_in_flight"] == 0
    # Cancelling drops claims; event states are untouched, and reconciled
    # (done) work is never resurrected.
    assert event_store.get_event(routed_event).state == "routed"
    assert event_store.get_event(reconciled_event).state == "reconciled"
    # No open manager items remain under any key.
    assert queue_store.list_open_items_for_role(TASK_MANAGER_ROLE) == []


def test_in_flight_items_are_left_alone(rekey_setup: dict) -> None:
    queue_store: SqlAlchemyAgentQueueStore = rekey_setup["queue_store"]
    event_store: SqlAlchemyTaskEventStore = rekey_setup["event_store"]

    event_id = _event(rekey_setup, seed="evt_flight", state="reconciled")
    legacy_key = _legacy_key(rekey_setup["owner"], rekey_setup["task_id"])
    item_id = _uid("item_flight")
    queue_store.enqueue(item_id, legacy_key, "notice", source_ids=[event_id], payload="n")
    now = now_epoch()
    queue_store.mark_dispatched(item_id, legacy_key, now=now)

    result = rekey_manager_queues(
        agent_queue_store=queue_store,
        task_event_store=event_store,
    )

    assert result["items_canceled"] == 0
    assert result["events_requeued"] == 0
    assert result["items_in_flight"] == 1
    # The in-flight item and its event are untouched — the status feed completes it.
    assert queue_store.get_item(item_id).state == "dispatched"
    assert event_store.get_event(event_id).state == "reconciled"


@pytest.mark.asyncio
async def test_repackaged_under_new_keys_after_sweep(rekey_setup: dict) -> None:
    """End-to-end: sweep, then the manager packager re-packages under new keys."""
    from omnigent.agent_tasks.queue.packagers import ManagerPackager, _StatusReader

    queue_store: SqlAlchemyAgentQueueStore = rekey_setup["queue_store"]
    task_store: SqlAlchemyTaskStore = rekey_setup["task_store"]
    event_store: SqlAlchemyTaskEventStore = rekey_setup["event_store"]

    event_id = _event(rekey_setup, seed="evt_e2e", state="routed")
    legacy_key = _legacy_key(rekey_setup["owner"], rekey_setup["task_id"])
    queue_store.enqueue(
        _uid("item_legacy"), legacy_key, "notice", source_ids=[event_id], payload="n"
    )

    rekey_manager_queues(agent_queue_store=queue_store, task_event_store=event_store)

    class _Idle(_StatusReader):
        def status_for(self, session_id: str) -> str | None:
            return "idle"

    packager = ManagerPackager(
        store=queue_store,
        task_event_store=event_store,
        task_store=task_store,
        status_reader=_Idle(),
        age_threshold_s=-1.0,
    )
    await packager.scan_once()

    new_key = AgentQueueKey(
        role=TASK_MANAGER_ROLE,
        owner_user_id=rekey_setup["owner"],
        scope_id=rekey_setup["manager_conv_id"],
    )
    items = queue_store.list_items(new_key)
    assert len(items) == 1
    assert items[0].source_ids == [event_id]
