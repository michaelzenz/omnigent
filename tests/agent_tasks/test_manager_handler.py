"""Tests for the manager dispatch handler."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from omnigent.agent_tasks.agent_builtins import TASK_MANAGER_ROLE
from omnigent.agent_tasks.queue.dispatcher import DispatchFailed, DispatchTarget
from omnigent.agent_tasks.queue.handlers import ManagerDispatchHandler
from omnigent.db.utils import generate_agent_id, now_epoch
from omnigent.entities import AgentQueueItem, AgentQueueKey
from omnigent.stores.agent_queue_store.sqlalchemy_store import SqlAlchemyAgentQueueStore
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


def _item(
    key: AgentQueueKey,
    *,
    item_id: str | None = None,
    payload: str = "[System: triage me]",
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


@pytest.fixture
def handler_setup(db_uri: str) -> dict:
    agent_store = SqlAlchemyAgentStore(db_uri)
    task_store = SqlAlchemyTaskStore(db_uri)
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
    task_id = _uid("task_one")
    task_store.create(
        task_id,
        "Handler task",
        "handler goal",
        owner_user_id="user-1",
        manager_conversation_id=manager_conv.id,
    )
    handler = ManagerDispatchHandler(
        store=queue_store,
        conversation_store=conversation_store,
        runner_router=None,
    )
    return {
        "handler": handler,
        "queue_store": queue_store,
        "task_store": task_store,
        "conversation_store": conversation_store,
        "agent_store": agent_store,
        "task_id": task_id,
        "manager_conv_id": manager_conv.id,
        "owner": "user-1",
    }


@pytest.mark.asyncio
async def test_resolve_target_returns_manager_session(handler_setup: dict) -> None:
    handler = handler_setup["handler"]
    key = AgentQueueKey(
        role=TASK_MANAGER_ROLE,
        owner_user_id=handler_setup["owner"],
        scope_id=handler_setup["manager_conv_id"],
    )
    target = await handler.resolve_target(_item(key))
    assert target.session_id == handler_setup["manager_conv_id"]


@pytest.mark.asyncio
async def test_resolve_target_caches_conversation_on_queue(handler_setup: dict) -> None:
    handler = handler_setup["handler"]
    queue_store: SqlAlchemyAgentQueueStore = handler_setup["queue_store"]
    key = AgentQueueKey(
        role=TASK_MANAGER_ROLE,
        owner_user_id=handler_setup["owner"],
        scope_id=handler_setup["manager_conv_id"],
    )
    # Enqueue so the queue row exists.
    queue_store.enqueue(_uid("e"), key, "notice", payload="x")
    await handler.resolve_target(_item(key))
    queue = queue_store.get_queue(key)
    assert queue is not None
    assert queue.conversation_id == handler_setup["manager_conv_id"]


@pytest.mark.asyncio
async def test_resolve_target_fails_when_conversation_missing(handler_setup: dict) -> None:
    handler = handler_setup["handler"]
    key = AgentQueueKey(
        role=TASK_MANAGER_ROLE,
        owner_user_id=handler_setup["owner"],
        scope_id=_uid("ghost_conv"),
    )
    with pytest.raises(DispatchFailed):
        await handler.resolve_target(_item(key))


@pytest.mark.asyncio
async def test_resolve_target_fails_when_scope_is_not_a_conversation(
    handler_setup: dict,
) -> None:
    """A legacy task-scoped key no longer resolves: scope must be a session."""
    handler = handler_setup["handler"]
    task_store: SqlAlchemyTaskStore = handler_setup["task_store"]
    task_id = _uid("task_no_mgr")
    task_store.create(
        task_id,
        "No manager task",
        "no manager goal",
        owner_user_id=handler_setup["owner"],
    )
    key = AgentQueueKey(
        role=TASK_MANAGER_ROLE,
        owner_user_id=handler_setup["owner"],
        scope_id=task_id,
    )
    with pytest.raises(DispatchFailed):
        await handler.resolve_target(_item(key))


@pytest.mark.asyncio
async def test_resolve_target_fails_when_conversation_gone(handler_setup: dict) -> None:
    handler = handler_setup["handler"]
    key = AgentQueueKey(
        role=TASK_MANAGER_ROLE,
        owner_user_id=handler_setup["owner"],
        scope_id=_uid("conv_missing"),
    )
    with pytest.raises(DispatchFailed):
        await handler.resolve_target(_item(key))


@pytest.mark.asyncio
async def test_deliver_calls_wake_parent(handler_setup: dict) -> None:
    handler = handler_setup["handler"]
    conv_id = handler_setup["manager_conv_id"]
    key = AgentQueueKey(
        role=TASK_MANAGER_ROLE,
        owner_user_id=handler_setup["owner"],
        scope_id=handler_setup["manager_conv_id"],
    )
    item = _item(key, payload="[System: triage me]")
    target = DispatchTarget(session_id=conv_id, harness="cursor-native")
    with patch(
        "omnigent.agent_tasks.queue.handlers._inject_notice",
        new_callable=AsyncMock,
    ) as inject:
        await handler.deliver(item, target)
    inject.assert_called_once()
    assert inject.call_args.kwargs["usage_purpose"] == "task_manager"


@pytest.mark.asyncio
async def test_deliver_fails_when_wake_returns_false(handler_setup: dict) -> None:
    handler = handler_setup["handler"]
    conv_id = handler_setup["manager_conv_id"]
    key = AgentQueueKey(
        role=TASK_MANAGER_ROLE,
        owner_user_id=handler_setup["owner"],
        scope_id=handler_setup["manager_conv_id"],
    )
    item = _item(key)
    target = DispatchTarget(session_id=conv_id)
    with patch(
        "omnigent.agent_tasks.queue.handlers._inject_notice",
        new_callable=AsyncMock,
        side_effect=DispatchFailed("wake returned false"),
    ):
        with pytest.raises(DispatchFailed):
            await handler.deliver(item, target)


@pytest.mark.asyncio
async def test_deliver_fails_without_payload(handler_setup: dict) -> None:
    handler = handler_setup["handler"]
    key = AgentQueueKey(
        role=TASK_MANAGER_ROLE,
        owner_user_id=handler_setup["owner"],
        scope_id=handler_setup["manager_conv_id"],
    )
    item = _item(key, payload="")
    target = DispatchTarget(session_id=handler_setup["manager_conv_id"])
    with pytest.raises(DispatchFailed):
        await handler.deliver(item, target)
