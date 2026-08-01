"""Tests for the secretary packager (poll-based batching)."""

from __future__ import annotations

import uuid

import pytest

from omnigent.agent_tasks.agent_builtins import TASK_SECRETARY_ROLE
from omnigent.agent_tasks.distributor import distribute_event
from omnigent.agent_tasks.queue.packagers import (
    DEFAULT_PACKAGER_AGE_THRESHOLD_S,
    DEFAULT_PACKAGER_POLL_INTERVAL_S,
    SecretaryPackager,
    _StatusReader,
    configure_secretary_packager,
    get_secretary_packager,
)
from omnigent.db.utils import generate_agent_id
from omnigent.entities import AgentQueueKey
from omnigent.stores.agent_queue_store.sqlalchemy_store import SqlAlchemyAgentQueueStore
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_role_profile_store.sqlalchemy_store import (
    SqlAlchemyTaskRoleProfileStore,
)
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


class _StaticStatusReader(_StatusReader):
    """Reports a fixed status for every session."""

    def __init__(self, status: str | None = "idle") -> None:
        self.status = status

    def status_for(self, session_id: str) -> str | None:
        return self.status


@pytest.fixture
def secretary_setup(db_uri: str) -> dict:
    agent_store = SqlAlchemyAgentStore(db_uri)
    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)
    profile_store = SqlAlchemyTaskRoleProfileStore(db_uri)
    queue_store = SqlAlchemyAgentQueueStore(db_uri)
    manager_agent_id = generate_agent_id()
    agent_store.create(
        manager_agent_id, name="task-manager-agent", bundle_location="test:///bundle"
    )
    user_id = "__anonymous__"
    secretary_conv = conversation_store.create_conversation(
        title="Secretary",
        agent_id=manager_agent_id,
        host_id=_uid("host_sec"),
        workspace="/tmp/secretary",
    )
    profile_store.upsert(
        user_id,
        "secretary",
        agent_profile_id=manager_agent_id,
        conversation_id=secretary_conv.id,
        host_id=_uid("host_sec"),
        workspace="/tmp/secretary",
    )
    status_reader = _StaticStatusReader("idle")
    packager = SecretaryPackager(
        store=queue_store,
        task_event_store=event_store,
        task_role_profile_store=profile_store,
        status_reader=status_reader,
        # Negative threshold so freshly-created events qualify immediately when
        # idle (age 0 > -1). Tests that need "wait because young" raise it.
        age_threshold_s=-1.0,
        batch_size=10,
    )
    configure_secretary_packager(packager)
    return {
        "agent_store": agent_store,
        "task_store": task_store,
        "event_store": event_store,
        "worker_store": worker_store,
        "conversation_store": conversation_store,
        "profile_store": profile_store,
        "queue_store": queue_store,
        "user_id": user_id,
        "secretary_conv_id": secretary_conv.id,
        "packager": packager,
        "status_reader": status_reader,
    }


@pytest.fixture(autouse=True)
def _clear_packager() -> None:
    yield
    configure_secretary_packager(None)


def _key(user_id: str) -> AgentQueueKey:
    return AgentQueueKey(role=TASK_SECRETARY_ROLE, owner_user_id=user_id)


@pytest.mark.asyncio
async def test_full_batch_sends_regardless_of_agent_state(secretary_setup: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = secretary_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = secretary_setup["queue_store"]
    packager: SecretaryPackager = secretary_setup["packager"]
    secretary_setup["status_reader"].status = "running"  # agent busy
    packager._batch_size = 3

    for i in range(3):
        event_store.create_event(
            _uid(f"evt{i}"),
            "build.finished",
            f"Ambiguous {i}",
            state="awaiting_grouping",
            owner_user_id=secretary_setup["user_id"],
        )
    packager.scan_once_sync()

    assert len(queue_store.list_items(_key(secretary_setup["user_id"]))) == 1


@pytest.mark.asyncio
async def test_partial_batch_waits_when_agent_busy(secretary_setup: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = secretary_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = secretary_setup["queue_store"]
    packager: SecretaryPackager = secretary_setup["packager"]
    secretary_setup["status_reader"].status = "running"
    packager._age_threshold_s = -1.0  # age floor would otherwise force a send

    event_store.create_event(
        _uid("evt"),
        "build.finished",
        "Ambiguous",
        state="awaiting_grouping",
        owner_user_id=secretary_setup["user_id"],
    )
    packager.scan_once_sync()

    assert queue_store.list_items(_key(secretary_setup["user_id"])) == []


@pytest.mark.asyncio
async def test_partial_batch_sends_when_idle_and_age_exceeded(secretary_setup: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = secretary_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = secretary_setup["queue_store"]
    packager: SecretaryPackager = secretary_setup["packager"]
    secretary_setup["status_reader"].status = "idle"
    packager._age_threshold_s = -1.0  # oldest age > 0 immediately

    event_store.create_event(
        _uid("evt"),
        "build.finished",
        "Ambiguous",
        state="awaiting_grouping",
        owner_user_id=secretary_setup["user_id"],
    )
    packager.scan_once_sync()

    items = queue_store.list_items(_key(secretary_setup["user_id"]))
    assert len(items) == 1
    assert "[System: task event(s) need routing" in items[0].payload


@pytest.mark.asyncio
async def test_partial_batch_waits_when_idle_but_young(secretary_setup: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = secretary_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = secretary_setup["queue_store"]
    packager: SecretaryPackager = secretary_setup["packager"]
    secretary_setup["status_reader"].status = "idle"
    packager._age_threshold_s = 3600  # far above any real age

    event_store.create_event(
        _uid("evt"),
        "build.finished",
        "Ambiguous",
        state="awaiting_grouping",
        owner_user_id=secretary_setup["user_id"],
    )
    packager.scan_once_sync()

    assert queue_store.list_items(_key(secretary_setup["user_id"])) == []


@pytest.mark.asyncio
async def test_stall_via_distributor_is_picked_up_by_poll(secretary_setup: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = secretary_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = secretary_setup["queue_store"]
    packager: SecretaryPackager = secretary_setup["packager"]
    secretary_setup["status_reader"].status = "idle"
    packager._age_threshold_s = -1.0

    event_id = _uid("stall_event")
    event = event_store.create_event(
        event_id,
        "build.finished",
        "Ambiguous build",
        state="received",
    )
    updated = await distribute_event(
        event=event,
        task_store=secretary_setup["task_store"],
        task_event_store=event_store,
        worker_store=secretary_setup["worker_store"],
        conversation_store=secretary_setup["conversation_store"],
        agent_store=secretary_setup["agent_store"],
        owner_user_id=secretary_setup["user_id"],
    )
    assert updated.state == "awaiting_grouping"
    assert updated.owner_user_id == secretary_setup["user_id"]
    packager.scan_once_sync()

    items = queue_store.list_items(_key(secretary_setup["user_id"]))
    assert len(items) == 1
    assert items[0].source_ids == [event_id]


@pytest.mark.asyncio
async def test_claimed_events_are_not_repackaged(secretary_setup: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = secretary_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = secretary_setup["queue_store"]
    packager: SecretaryPackager = secretary_setup["packager"]
    secretary_setup["status_reader"].status = "idle"
    packager._age_threshold_s = -1.0

    event_store.create_event(
        _uid("evt"),
        "build.finished",
        "Ambiguous",
        state="awaiting_grouping",
        owner_user_id=secretary_setup["user_id"],
    )
    packager.scan_once_sync()  # packages it
    packager.scan_once_sync()  # should not duplicate

    assert len(queue_store.list_items(_key(secretary_setup["user_id"]))) == 1


@pytest.mark.asyncio
async def test_stale_events_routed_away_are_filtered(secretary_setup: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = secretary_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = secretary_setup["queue_store"]
    packager: SecretaryPackager = secretary_setup["packager"]
    secretary_setup["status_reader"].status = "idle"
    packager._age_threshold_s = -1.0

    event = event_store.create_event(
        _uid("stale"),
        "build.finished",
        "Already routed",
        state="awaiting_grouping",
        owner_user_id=secretary_setup["user_id"],
    )
    event_store.update_event(event.id, state="routed")
    packager.scan_once_sync()

    assert queue_store.list_items(_key(secretary_setup["user_id"])) == []


@pytest.mark.asyncio
async def test_no_live_secretary_holds_events(secretary_setup: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = secretary_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = secretary_setup["queue_store"]
    packager: SecretaryPackager = secretary_setup["packager"]
    # A user with no secretary profile.
    event_store.create_event(
        _uid("orphan"),
        "build.finished",
        "Ambiguous",
        state="awaiting_grouping",
        owner_user_id="nobody",
    )
    packager.scan_once_sync()

    assert queue_store.list_items(_key("nobody")) == []


def test_defaults_are_configurable_constants() -> None:
    assert DEFAULT_PACKAGER_POLL_INTERVAL_S == 5.0
    assert DEFAULT_PACKAGER_AGE_THRESHOLD_S == 15


@pytest.mark.asyncio
async def test_orphan_session_event_is_packaged_like_any_stall(
    secretary_setup: dict,
) -> None:
    """An orphan session becomes an awaiting_grouping event the packager polls."""
    from omnigent.agent_tasks.adoption import (
        SessionAdoptionContext,
        configure_session_adoption,
        enqueue_orphan_session,
    )
    from omnigent.agent_tasks.event_types import SESSION_ORPHAN_EVENT_TYPE

    event_store: SqlAlchemyTaskEventStore = secretary_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = secretary_setup["queue_store"]
    packager: SecretaryPackager = secretary_setup["packager"]
    secretary_setup["status_reader"].status = "idle"
    packager._age_threshold_s = -1.0

    configure_session_adoption(
        SessionAdoptionContext(
            task_store=secretary_setup["task_store"],
            task_event_store=event_store,
            worker_store=secretary_setup["worker_store"],
            conversation_store=secretary_setup["conversation_store"],
        )
    )
    conv = secretary_setup["conversation_store"].create_conversation(
        title="Mystery session",
        agent_id=secretary_setup["agent_store"].get_by_name("task-manager-agent").id,
        host_id=_uid("host_orphan"),
        workspace="/tmp/mystery",
    )
    await enqueue_orphan_session(conv.id, owner_user_id=secretary_setup["user_id"])
    packager.scan_once_sync()

    items = queue_store.list_items(_key(secretary_setup["user_id"]))
    assert len(items) == 1
    assert "session.orphan" in items[0].payload
    assert "Mystery session" in items[0].payload
    assert "routing_repo" in items[0].payload  # orphan-specific guidance
    # The packaged source is the orphan event, not the session id directly.
    orphan_events = event_store.list_events(
        state="awaiting_grouping", event_type=SESSION_ORPHAN_EVENT_TYPE
    )
    assert items[0].source_ids == [orphan_events[0].id]


def test_get_secretary_packager_returns_configured(secretary_setup: dict) -> None:
    assert get_secretary_packager() is secretary_setup["packager"]


def test_get_secretary_packager_none_when_unconfigured() -> None:
    configure_secretary_packager(None)
    assert get_secretary_packager() is None
