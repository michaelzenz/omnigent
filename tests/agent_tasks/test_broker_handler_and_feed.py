"""Tests for the broker dispatch handler and the queue status feed."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from omnigent.agent_tasks.agent_builtins import TASK_BROKER_ROLE
from omnigent.agent_tasks.queue.dispatcher import DispatchFailed, DispatchTarget
from omnigent.agent_tasks.queue.handlers import BrokerDispatchHandler
from omnigent.agent_tasks.queue.status_feed import QueueStatusFeed
from omnigent.db.utils import generate_agent_id, now_epoch
from omnigent.entities import AgentQueueItem, AgentQueueKey
from omnigent.stores.agent_queue_store.sqlalchemy_store import SqlAlchemyAgentQueueStore
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.user_role_session_store.sqlalchemy_store import (
    SqlAlchemyUserRoleSessionStore,
)


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


def _item(
    key: AgentQueueKey,
    *,
    item_id: str | None = None,
    payload: str = "[System: test notice]",
) -> AgentQueueItem:
    return AgentQueueItem(
        id=item_id or _uid("item"),
        role=key.role,
        owner_user_id=key.owner_user_id,
        scope_id=key.scope_id,
        kind="notice",
        state="queued",
        created_at=now_epoch(),
        source_ids=[],
        payload=payload,
        seq=0,
    )


# ── BrokerDispatchHandler ───────────────────────


@pytest.fixture
def handler_setup(db_uri: str) -> dict:
    agent_store = SqlAlchemyAgentStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)
    session_store = SqlAlchemyUserRoleSessionStore(db_uri)
    queue_store = SqlAlchemyAgentQueueStore(db_uri)
    manager_agent_id = generate_agent_id()
    agent_store.create(
        manager_agent_id, name="task-manager-agent", bundle_location="test:///bundle"
    )
    conv = conversation_store.create_conversation(
        title="Task broker",
        agent_id=manager_agent_id,
        host_id=_uid("host"),
        workspace="/tmp/broker",
    )
    user_id = "user-1"
    session_store.set_conversation(user_id, TASK_BROKER_ROLE, conv.id)
    handler = BrokerDispatchHandler(
        store=queue_store,
        user_role_session_store=session_store,
        conversation_store=conversation_store,
        runner_router=None,
    )
    return {
        "handler": handler,
        "queue_store": queue_store,
        "conversation_store": conversation_store,
        "session_store": session_store,
        "agent_store": agent_store,
        "conv_id": conv.id,
        "user_id": user_id,
    }


@pytest.mark.asyncio
async def test_resolve_target_returns_broker_session(handler_setup: dict) -> None:
    handler = handler_setup["handler"]
    key = AgentQueueKey(role=TASK_BROKER_ROLE, owner_user_id=handler_setup["user_id"])
    target = await handler.resolve_target(_item(key))
    assert target.session_id == handler_setup["conv_id"]


@pytest.mark.asyncio
async def test_resolve_target_caches_conversation_on_queue(handler_setup: dict) -> None:
    handler = handler_setup["handler"]
    queue_store: SqlAlchemyAgentQueueStore = handler_setup["queue_store"]
    key = AgentQueueKey(role=TASK_BROKER_ROLE, owner_user_id=handler_setup["user_id"])
    # Enqueue so the queue row exists.
    queue_store.enqueue(_uid("e"), key, "notice", payload="x")
    await handler.resolve_target(_item(key))
    queue = queue_store.get_queue(key)
    assert queue is not None
    assert queue.conversation_id == handler_setup["conv_id"]


@pytest.mark.asyncio
async def test_resolve_target_fails_without_session(handler_setup: dict) -> None:
    handler = handler_setup["handler"]
    key = AgentQueueKey(role=TASK_BROKER_ROLE, owner_user_id="nobody")
    with pytest.raises(DispatchFailed):
        await handler.resolve_target(_item(key))


@pytest.mark.asyncio
async def test_resolve_target_fails_when_conversation_gone(handler_setup: dict) -> None:
    handler = handler_setup["handler"]
    session_store: SqlAlchemyUserRoleSessionStore = handler_setup["session_store"]
    user_id = "user-2"
    # The binding points at a conversation that does not exist.
    session_store.set_conversation(user_id, TASK_BROKER_ROLE, _uid("conv_missing"))
    key = AgentQueueKey(role=TASK_BROKER_ROLE, owner_user_id=user_id)
    with pytest.raises(DispatchFailed):
        await handler.resolve_target(_item(key))


@pytest.mark.asyncio
async def test_deliver_calls_wake_parent(handler_setup: dict) -> None:
    handler = handler_setup["handler"]
    conv_id = handler_setup["conv_id"]
    key = AgentQueueKey(role=TASK_BROKER_ROLE, owner_user_id=handler_setup["user_id"])
    item = _item(key, payload="[System: route me]")
    target = DispatchTarget(session_id=conv_id, harness="claude-native")
    with patch(
        "omnigent.server.routes.sessions._wake_parent_for_blocked_child",
        new_callable=AsyncMock,
        return_value=True,
    ) as wake:
        await handler.deliver(item, target)
    wake.assert_called_once()
    assert wake.call_args.args[0] == conv_id
    assert wake.call_args.args[2] == "[System: route me]"


@pytest.mark.asyncio
async def test_deliver_fails_when_wake_returns_false(handler_setup: dict) -> None:
    handler = handler_setup["handler"]
    conv_id = handler_setup["conv_id"]
    key = AgentQueueKey(role=TASK_BROKER_ROLE, owner_user_id=handler_setup["user_id"])
    item = _item(key)
    target = DispatchTarget(session_id=conv_id)
    with patch(
        "omnigent.server.routes.sessions._wake_parent_for_blocked_child",
        new_callable=AsyncMock,
        return_value=False,
    ):
        with pytest.raises(DispatchFailed):
            await handler.deliver(item, target)


@pytest.mark.asyncio
async def test_deliver_fails_without_payload(handler_setup: dict) -> None:
    handler = handler_setup["handler"]
    key = AgentQueueKey(role=TASK_BROKER_ROLE, owner_user_id=handler_setup["user_id"])
    item = _item(key, payload="")
    target = DispatchTarget(session_id=handler_setup["conv_id"])
    with pytest.raises(DispatchFailed):
        await handler.deliver(item, target)


# ── QueueStatusFeed ─────────────────────────────────


@pytest.mark.asyncio
async def test_status_feed_completes_inflight_on_idle(db_uri: str) -> None:
    queue_store = SqlAlchemyAgentQueueStore(db_uri)
    key = AgentQueueKey(role=TASK_BROKER_ROLE, owner_user_id="user-x")
    session_id = "22222222222222222222222222222222"
    queue_store.enqueue(_uid("e"), key, "notice", payload="x")
    queue_store.set_queue_conversation(key, session_id)
    item = queue_store.list_items(key)[0]
    queue_store.mark_dispatched(item.id, key, now=now_epoch())

    feed = QueueStatusFeed(queue_store)
    await feed.notify(session_id, "running")
    queue = queue_store.get_queue(key)
    assert queue is not None
    assert queue.inflight_item_id is not None  # still in flight

    await feed.notify(session_id, "idle")
    queue = queue_store.get_queue(key)
    assert queue is not None
    assert queue.inflight_item_id is None
    done_item = queue_store.get_item(item.id)
    assert done_item is not None
    assert done_item.state == "done"


@pytest.mark.asyncio
async def test_status_feed_completes_inflight_on_failed(db_uri: str) -> None:
    queue_store = SqlAlchemyAgentQueueStore(db_uri)
    key = AgentQueueKey(role=TASK_BROKER_ROLE, owner_user_id="user-y")
    session_id = "33333333333333333333333333333333"
    queue_store.enqueue(_uid("e"), key, "notice", payload="x")
    queue_store.set_queue_conversation(key, session_id)
    item = queue_store.list_items(key)[0]
    queue_store.mark_dispatched(item.id, key, now=now_epoch())

    feed = QueueStatusFeed(queue_store)
    await feed.notify(session_id, "failed")

    queue = queue_store.get_queue(key)
    assert queue is not None
    assert queue.inflight_item_id is None
    done_item = queue_store.get_item(item.id)
    assert done_item is not None
    assert done_item.state == "done"


@pytest.mark.asyncio
async def test_status_feed_noop_for_session_without_queue(db_uri: str) -> None:
    queue_store = SqlAlchemyAgentQueueStore(db_uri)
    feed = QueueStatusFeed(queue_store)
    # Should not raise.
    await feed.notify("never_seen_session", "idle")


@pytest.mark.asyncio
async def test_status_feed_ignores_running_status(db_uri: str) -> None:
    queue_store = SqlAlchemyAgentQueueStore(db_uri)
    key = AgentQueueKey(role=TASK_BROKER_ROLE, owner_user_id="user-z")
    session_id = "44444444444444444444444444444444"
    queue_store.enqueue(_uid("e"), key, "notice", payload="x")
    queue_store.set_queue_conversation(key, session_id)
    item = queue_store.list_items(key)[0]
    queue_store.mark_dispatched(item.id, key, now=now_epoch())

    feed = QueueStatusFeed(queue_store)
    await feed.notify(session_id, "running")
    queue = queue_store.get_queue(key)
    assert queue is not None
    assert queue.inflight_item_id is not None  # unchanged


@pytest.mark.asyncio
async def test_status_feed_pushes_to_gate_callback(db_uri: str) -> None:
    queue_store = SqlAlchemyAgentQueueStore(db_uri)
    observed: list[tuple[str, str]] = []
    feed = QueueStatusFeed(queue_store, on_status=lambda sid, st: observed.append((sid, st)))
    await feed.notify("sess-1", "idle")
    await feed.notify("sess-2", "running")
    assert observed == [("sess-1", "idle"), ("sess-2", "running")]
