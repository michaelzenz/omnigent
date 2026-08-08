"""Tests for the orphan-session queue model.

Orphan sessions are now durable ``session.orphan`` task events the broker
packager polls — no in-memory queue, debounce, or direct wake.
"""

from __future__ import annotations

import uuid

import pytest

from omnigent.agent_tasks.adoption import (
    SessionAdoptionContext,
    configure_session_adoption,
    enqueue_orphan_session,
    find_open_orphan_event,
    propose_session_adoption,
)
from omnigent.agent_tasks.event_types import SESSION_ORPHAN_EVENT_TYPE
from omnigent.db.utils import generate_agent_id
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.fixture
def orphan_setup(db_uri: str) -> dict:
    agent_store = SqlAlchemyAgentStore(db_uri)
    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)

    manager_agent_id = generate_agent_id()
    agent_store.create(
        manager_agent_id, name="task-manager-agent", bundle_location="test:///bundle"
    )
    conv = conversation_store.create_conversation(
        title="Orphan session",
        agent_id=manager_agent_id,
        host_id=_uid("host"),
        workspace="/tmp/orphan",
    )
    configure_session_adoption(
        SessionAdoptionContext(
            task_store=task_store,
            task_event_store=event_store,
            worker_store=worker_store,
            conversation_store=conversation_store,
        )
    )
    return {
        "agent_store": agent_store,
        "task_store": task_store,
        "event_store": event_store,
        "conversation_store": conversation_store,
        "worker_store": worker_store,
        "manager_agent_id": manager_agent_id,
        "session_id": conv.id,
        "owner": "user-orphan",
    }


@pytest.fixture(autouse=True)
def _clear_adoption() -> None:
    yield
    configure_session_adoption(None)


@pytest.mark.asyncio
async def test_enqueue_creates_awaiting_grouping_orphan_event(orphan_setup: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = orphan_setup["event_store"]
    queued = await enqueue_orphan_session(
        orphan_setup["session_id"],
        owner_user_id=orphan_setup["owner"],
    )
    assert queued is True

    orphan = find_open_orphan_event(event_store, orphan_setup["session_id"])
    assert orphan is not None
    assert orphan.event_type == SESSION_ORPHAN_EVENT_TYPE
    assert orphan.state == "awaiting_grouping"
    assert orphan.source_key == orphan_setup["session_id"]
    assert orphan.owner_user_id == orphan_setup["owner"]
    assert "Orphan session" in orphan.title


@pytest.mark.asyncio
async def test_enqueue_dedups_open_orphan_event(orphan_setup: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = orphan_setup["event_store"]
    assert (
        await enqueue_orphan_session(
            orphan_setup["session_id"], owner_user_id=orphan_setup["owner"]
        )
        is True
    )
    # A second enqueue for the same session is a no-op while the event is open.
    assert (
        await enqueue_orphan_session(
            orphan_setup["session_id"], owner_user_id=orphan_setup["owner"]
        )
        is False
    )
    orphans = event_store.list_events(
        state="awaiting_grouping", event_type=SESSION_ORPHAN_EVENT_TYPE
    )
    assert len(orphans) == 1


@pytest.mark.asyncio
async def test_enqueue_skips_non_orphan_conversations(orphan_setup: dict) -> None:
    conversation_store: SqlAlchemyConversationStore = orphan_setup["conversation_store"]
    # A sub-agent conversation is not an orphan candidate.
    sub = conversation_store.create_conversation(
        kind="sub_agent",
        title="Worker",
        parent_conversation_id=orphan_setup["session_id"],
        agent_id=orphan_setup["manager_agent_id"],
        host_id=_uid("host_sub"),
        workspace="/tmp/sub",
    )
    queued = await enqueue_orphan_session(sub.id, owner_user_id=orphan_setup["owner"])
    assert queued is False


@pytest.mark.asyncio
async def test_propose_adoption_reconciles_orphan_event(orphan_setup: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = orphan_setup["event_store"]
    task_store: SqlAlchemyTaskStore = orphan_setup["task_store"]
    conversation_store: SqlAlchemyConversationStore = orphan_setup["conversation_store"]
    worker_store: SqlAlchemyWorkerStore = orphan_setup["worker_store"]

    await enqueue_orphan_session(orphan_setup["session_id"], owner_user_id=orphan_setup["owner"])
    assert find_open_orphan_event(event_store, orphan_setup["session_id"]) is not None

    # Give the session routing tags so propose_session_adoption accepts it.
    conversation_store.set_labels(
        orphan_setup["session_id"], {"omnigent.task.routing_repo": "repo-x"}
    )
    task_id = _uid("task_adopt")
    task_store.create(
        task_id,
        "Adopt target",
        owner_user_id=orphan_setup["owner"],
    )
    proposal = propose_session_adoption(
        session_id=orphan_setup["session_id"],
        task_store=task_store,
        task_event_store=event_store,
        worker_store=worker_store,
        conversation_store=conversation_store,
        owner_user_id=orphan_setup["owner"],
    )
    assert proposal.event_type == "session.adoption"
    # The orphan trigger event is reconciled, so the packager will not repackage it.
    assert find_open_orphan_event(event_store, orphan_setup["session_id"]) is None
    # The orphan event row still exists but is no longer awaiting_grouping.
    all_orphans = event_store.list_events(event_type=SESSION_ORPHAN_EVENT_TYPE)
    assert len(all_orphans) == 1
    assert all_orphans[0].state == "reconciled"
