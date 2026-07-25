"""Tests for the distributor in-memory queue."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from omnigent.agent_tasks.distributor import distribute_event
from omnigent.agent_tasks.distributor_queue import (
    DistributorQueueContext,
    configure_distributor_queue,
    start_distributor_consumer,
    stop_distributor_consumer,
)
from omnigent.agent_tasks.distributor_session import clear_distributor_conversation_cache
from omnigent.db.utils import generate_agent_id
from omnigent.entities import TaskTag
from omnigent.entities.secretary import UserSecretaryProfile
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.secretary_profile_store.sqlalchemy_store import SqlAlchemySecretaryProfileStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


@pytest.fixture
async def distributor_queue(db_uri: str) -> dict:
    agent_store = SqlAlchemyAgentStore(db_uri)
    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)
    secretary_store = SqlAlchemySecretaryProfileStore(db_uri)
    manager_agent_id = generate_agent_id()
    agent_store.create(manager_agent_id, name="task-manager-agent", bundle_location="test:///bundle")
    distributor_agent_id = generate_agent_id()
    agent_store.create(
        distributor_agent_id,
        name="task-distributor",
        bundle_location="test:///bundle",
    )
    task_id = _uid("queue_task")
    task_store.create(
        task_id,
        manager_agent_id,
        "Upload retries",
        charter="flaky upload retries repo:omnigent-fork",
        tags=[TaskTag(task_id=task_id, tag_type="repo", tag="omnigent-fork")],
    )
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
    configure_distributor_queue(
        DistributorQueueContext(
            task_store=task_store,
            task_event_store=event_store,
            conversation_store=conversation_store,
            agent_store=agent_store,
            secretary_profile_store=secretary_store,
            runner_router=None,
        )
    )
    clear_distributor_conversation_cache()
    await start_distributor_consumer()
    yield {
        "agent_store": agent_store,
        "task_store": task_store,
        "event_store": event_store,
        "conversation_store": conversation_store,
        "secretary_store": secretary_store,
        "task_id": task_id,
        "user_id": user_id,
    }
    await stop_distributor_consumer()
    configure_distributor_queue(None)
    clear_distributor_conversation_cache()


@pytest.mark.asyncio
async def test_distributor_flag_on_enqueues_without_secretary_wake(
    monkeypatch: pytest.MonkeyPatch,
    db_uri: str,
    distributor_queue: dict,
) -> None:
    monkeypatch.setattr(
        "omnigent.agent_tasks.constants.distributor_agent_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "omnigent.agent_tasks.distributor.distributor_agent_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "omnigent.agent_tasks.distributor_queue.distributor_agent_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "omnigent.agent_tasks.distributor_queue.DISTRIBUTOR_BATCH_DEBOUNCE_SECONDS",
        0,
    )
    event_store: SqlAlchemyTaskEventStore = distributor_queue["event_store"]
    event_id = _uid("queued_event")
    event = event_store.create_event(
        event_id,
        "build.finished",
        "Ambiguous build",
        summary="repo omnigent-fork maybe upload",
        state="received",
    )
    with patch(
        "omnigent.agent_tasks.distributor_queue.wake_secretary_for_stalled_events",
        new_callable=AsyncMock,
    ) as wake_secretary:
        with patch(
            "omnigent.agent_tasks.distributor_queue.wake_distributor_for_batch",
            new_callable=AsyncMock,
            return_value=True,
        ) as wake_distributor:
            updated = await distribute_event(
                event=event,
                task_store=distributor_queue["task_store"],
                task_event_store=event_store,
                conversation_store=distributor_queue["conversation_store"],
                agent_store=distributor_queue["agent_store"],
                runner_router=None,
                secretary_profile_store=distributor_queue["secretary_store"],
                owner_user_id=distributor_queue["user_id"],
            )
            await asyncio.sleep(0.05)
    assert updated.state == "awaiting_grouping"
    wake_secretary.assert_not_called()
    wake_distributor.assert_called_once()
    call_kwargs = wake_distributor.call_args.kwargs
    assert len(call_kwargs["events"]) == 1
    assert call_kwargs["events"][0].id == event_id


@pytest.mark.asyncio
async def test_distributor_flag_on_skips_keyword_auto_route(
    monkeypatch: pytest.MonkeyPatch,
    db_uri: str,
    distributor_queue: dict,
) -> None:
    monkeypatch.setattr(
        "omnigent.agent_tasks.constants.distributor_agent_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "omnigent.agent_tasks.distributor.distributor_agent_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "omnigent.agent_tasks.distributor_queue.distributor_agent_enabled",
        lambda: True,
    )
    event_store: SqlAlchemyTaskEventStore = distributor_queue["event_store"]
    event_id = _uid("no_auto_event")
    event = event_store.create_event(
        event_id,
        "build.finished",
        "Upload retries failed",
        summary="repo omnigent-fork upload flaky",
        state="received",
    )
    updated = await distribute_event(
        event=event,
        task_store=distributor_queue["task_store"],
        task_event_store=event_store,
        conversation_store=distributor_queue["conversation_store"],
        agent_store=distributor_queue["agent_store"],
        runner_router=None,
        secretary_profile_store=distributor_queue["secretary_store"],
        owner_user_id=distributor_queue["user_id"],
    )
    assert updated.state == "awaiting_grouping"
    assert updated.task_id is None
