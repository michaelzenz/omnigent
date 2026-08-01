"""Tests for the secretary in-memory wake queue."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from omnigent.agent_tasks.distributor import distribute_event
from omnigent.agent_tasks.secretary_queue import (
    SecretaryQueueContext,
    configure_secretary_queue,
    flush_secretary_queue_for_tests,
    start_secretary_consumer,
    stop_secretary_consumer,
)
from omnigent.db.utils import generate_agent_id
from omnigent.entities import TaskTag
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.secretary_profile_store.sqlalchemy_store import SqlAlchemySecretaryProfileStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.fixture
async def secretary_queue(db_uri: str) -> dict:
    agent_store = SqlAlchemyAgentStore(db_uri)
    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)
    secretary_store = SqlAlchemySecretaryProfileStore(db_uri)
    manager_agent_id = generate_agent_id()
    agent_store.create(manager_agent_id, name="task-manager-agent", bundle_location="test:///bundle")
    user_id = "__anonymous__"
    secretary_conv = conversation_store.create_conversation(
        title="Secretary",
        agent_id=manager_agent_id,
        host_id=_uid("host_sec"),
        workspace="/tmp/secretary",
    )
    secretary_store.upsert(
        user_id,
        agent_id=manager_agent_id,
        conversation_id=secretary_conv.id,
        host_id=_uid("host_sec"),
        workspace="/tmp/secretary",
    )
    configure_secretary_queue(
        SecretaryQueueContext(
            task_event_store=event_store,
            conversation_store=conversation_store,
            secretary_profile_store=secretary_store,
            runner_router=None,
        )
    )
    await start_secretary_consumer()
    yield {
        "agent_store": agent_store,
        "task_store": task_store,
        "event_store": event_store,
        "worker_store": worker_store,
        "conversation_store": conversation_store,
        "secretary_store": secretary_store,
        "user_id": user_id,
    }
    await stop_secretary_consumer()
    configure_secretary_queue(None)


@pytest.mark.asyncio
async def test_stall_enqueues_secretary_wake(
    monkeypatch: pytest.MonkeyPatch,
    secretary_queue: dict,
) -> None:
    monkeypatch.setattr(
        "omnigent.agent_tasks.secretary_queue.SECRETARY_BATCH_DEBOUNCE_SECONDS",
        0,
    )
    event_store: SqlAlchemyTaskEventStore = secretary_queue["event_store"]
    event_id = _uid("queued_event")
    event = event_store.create_event(
        event_id,
        "build.finished",
        "Ambiguous build",
        state="received",
    )
    with patch(
        "omnigent.agent_tasks.secretary_queue.wake_secretary_for_stalled_events",
        new_callable=AsyncMock,
        return_value=True,
    ) as wake_secretary:
        updated = await distribute_event(
            event=event,
            task_store=secretary_queue["task_store"],
            task_event_store=event_store,
            worker_store=secretary_queue["worker_store"],
            conversation_store=secretary_queue["conversation_store"],
            agent_store=secretary_queue["agent_store"],
            runner_router=None,
            owner_user_id=secretary_queue["user_id"],
        )
        await asyncio.sleep(0.05)
        await flush_secretary_queue_for_tests()
    assert updated.state == "awaiting_grouping"
    wake_secretary.assert_called_once()
    call_kwargs = wake_secretary.call_args.kwargs
    assert len(call_kwargs["events"]) == 1
    assert call_kwargs["events"][0].id == event_id
