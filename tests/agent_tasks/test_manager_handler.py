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
        agent_profile_id=manager_agent_id,
        owner_user_id="user-1",
        manager_conversation_id=manager_conv.id,
    )
    handler = ManagerDispatchHandler(
        store=queue_store,
        task_store=task_store,
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
        scope_id=handler_setup["task_id"],
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
        scope_id=handler_setup["task_id"],
    )
    # Enqueue so the queue row exists.
    queue_store.enqueue(_uid("e"), key, "notice", payload="x")
    await handler.resolve_target(_item(key))
    queue = queue_store.get_queue(key)
    assert queue is not None
    assert queue.conversation_id == handler_setup["manager_conv_id"]


@pytest.mark.asyncio
async def test_resolve_target_fails_when_task_missing(handler_setup: dict) -> None:
    handler = handler_setup["handler"]
    key = AgentQueueKey(
        role=TASK_MANAGER_ROLE,
        owner_user_id=handler_setup["owner"],
        scope_id=_uid("ghost_task"),
    )
    with pytest.raises(DispatchFailed):
        await handler.resolve_target(_item(key))


@pytest.mark.asyncio
async def test_resolve_target_fails_when_no_manager_conversation(
    handler_setup: dict,
) -> None:
    handler = handler_setup["handler"]
    task_store: SqlAlchemyTaskStore = handler_setup["task_store"]
    task_id = _uid("task_no_mgr")
    task_store.create(
        task_id,
        "No manager task",
        agent_profile_id=handler_setup["agent_store"].get_by_name("task-manager-agent").id,
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
    task_store: SqlAlchemyTaskStore = handler_setup["task_store"]
    task_id = _uid("task_stale")
    task_store.create(
        task_id,
        "Stale task",
        agent_profile_id=handler_setup["agent_store"].get_by_name("task-manager-agent").id,
        owner_user_id=handler_setup["owner"],
        manager_conversation_id=_uid("conv_missing"),
    )
    key = AgentQueueKey(
        role=TASK_MANAGER_ROLE,
        owner_user_id=handler_setup["owner"],
        scope_id=task_id,
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
        scope_id=handler_setup["task_id"],
    )
    item = _item(key, payload="[System: triage me]")
    target = DispatchTarget(session_id=conv_id, harness="cursor-native")
    with patch(
        "omnigent.server.routes.sessions._wake_parent_for_blocked_child",
        new_callable=AsyncMock,
        return_value=True,
    ) as wake:
        await handler.deliver(item, target)
    wake.assert_called_once()
    assert wake.call_args.args[0] == conv_id
    assert wake.call_args.args[2] == "[System: triage me]"


@pytest.mark.asyncio
async def test_deliver_fails_when_wake_returns_false(handler_setup: dict) -> None:
    handler = handler_setup["handler"]
    conv_id = handler_setup["manager_conv_id"]
    key = AgentQueueKey(
        role=TASK_MANAGER_ROLE,
        owner_user_id=handler_setup["owner"],
        scope_id=handler_setup["task_id"],
    )
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
    key = AgentQueueKey(
        role=TASK_MANAGER_ROLE,
        owner_user_id=handler_setup["owner"],
        scope_id=handler_setup["task_id"],
    )
    item = _item(key, payload="")
    target = DispatchTarget(session_id=handler_setup["manager_conv_id"])
    with pytest.raises(DispatchFailed):
        await handler.deliver(item, target)
