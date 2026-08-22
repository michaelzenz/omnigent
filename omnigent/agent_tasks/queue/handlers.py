"""Per-role dispatch handlers — turn an :class:`AgentQueueItem` into a delivery.

The dispatcher knows *when* to send (gate, lease); a handler knows *how*. Each role
gets one handler registered under its role name in :class:`DispatcherContext.handlers`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from omnigent.agent_tasks.agent_builtins import (
    TASK_BROKER_ROLE,
)
from omnigent.agent_tasks.dispatch import (
    dispatch_worker_for_item,
    resolve_dispatch_params,
)
from omnigent.agent_tasks.executions import complete_execution
from omnigent.agent_tasks.internal_worker import initialize_internal_worker
from omnigent.agent_tasks.items import ensure_task_manager_for_dispatch
from omnigent.agent_tasks.manager_role_profile import load_manager_role_profile
from omnigent.agent_tasks.queue.dispatcher import (
    DispatchFailed,
    DispatchTarget,
    RoleDispatchHandler,
)
from omnigent.agent_tasks.task_activity import sync_task_activity_state
from omnigent.entities import AgentQueueItem
from omnigent.runner.routing import RunnerRouter
from omnigent.stores.agent_queue_store import AgentQueueStore
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_item_store import TaskItemStore
from omnigent.stores.task_role_profile_store import TaskRoleProfileStore
from omnigent.stores.task_store import TaskStore
from omnigent.stores.user_role_session_store import UserRoleSessionStore
from omnigent.stores.worker_store import WorkerStore

_logger = logging.getLogger(__name__)


async def _inject_notice(
    item: AgentQueueItem,
    target: DispatchTarget,
    *,
    conversation_store: ConversationStore,
    runner_router: RunnerRouter | None,
) -> None:
    """Inject ``item.payload`` into the target session as a synthetic user message.

    Shared by every role whose payload is a ``[System: …]`` notice — broker and
    manager today. Raises :class:`DispatchFailed` when the payload is empty, the
    conversation is gone, or the runner refuses the injection.
    """
    from omnigent.server.routes.sessions import _wake_parent_for_blocked_child
    from omnigent.usage_ledger import TASK_EVENT_ROUTING_PURPOSE

    if not item.payload:
        raise DispatchFailed("notice item has no payload to deliver")
    if target.session_id is None:
        raise DispatchFailed("notice target has no conversation")
    conv = await asyncio.to_thread(
        conversation_store.get_conversation,
        target.session_id,
    )
    if conv is None:
        raise DispatchFailed(f"target conversation {target.session_id} missing at deliver time")
    ok = await _wake_parent_for_blocked_child(
        target.session_id,
        conv,
        item.payload,
        conversation_store=conversation_store,
        runner_router=runner_router,
        usage_purpose=TASK_EVENT_ROUTING_PURPOSE,
    )
    if not ok:
        raise DispatchFailed(f"notice delivery to {target.session_id} returned false")


class BrokerDispatchHandler(RoleDispatchHandler):
    """Deliver broker notices to the user's live broker session.

    The target is the broker's bound conversation from the role profile. The
    handler caches it on the queue row via :meth:`set_queue_conversation` so the
    status feed can reverse-look-up the queue from the session id when the
    broker goes idle.
    """

    def __init__(
        self,
        store: AgentQueueStore,
        user_role_session_store: UserRoleSessionStore,
        conversation_store: ConversationStore,
        runner_router: RunnerRouter | None,
    ) -> None:
        self._store = store
        self._user_role_session_store = user_role_session_store
        self._conversation_store = conversation_store
        self._runner_router = runner_router

    async def resolve_target(self, item: AgentQueueItem) -> DispatchTarget:
        session = self._user_role_session_store.get(
            item.key.owner_user_id,
            TASK_BROKER_ROLE,
        )
        if session is None or session.conversation_id is None:
            raise DispatchFailed(f"no live broker for user {item.key.owner_user_id}")
        conversation_id = session.conversation_id
        conv = await asyncio.to_thread(
            self._conversation_store.get_conversation,
            conversation_id,
        )
        if conv is None:
            raise DispatchFailed(f"broker conversation {conversation_id} missing")
        # Cache the target so the status feed can find this queue from the
        # session id alone when the broker goes idle.
        self._store.set_queue_conversation(item.key, conversation_id)
        harness = conv.harness_override or "cursor-native"
        return DispatchTarget(
            session_id=conversation_id,
            harness=harness,
        )

    async def deliver(self, item: AgentQueueItem, target: DispatchTarget) -> None:
        await _inject_notice(
            item,
            target,
            conversation_store=self._conversation_store,
            runner_router=self._runner_router,
        )


class ManagerDispatchHandler(RoleDispatchHandler):
    """Deliver manager notices to a task's manager session.

    The target is the task's ``manager_conversation_id`` (one manager
    conversation per task), so the queue's ``scope_id`` is the task id. The
    handler caches the conversation on the queue row for the status feed's
    reverse look-up, the same way the broker handler does.
    """

    def __init__(
        self,
        store: AgentQueueStore,
        task_store: TaskStore,
        conversation_store: ConversationStore,
        runner_router: RunnerRouter | None,
    ) -> None:
        self._store = store
        self._task_store = task_store
        self._conversation_store = conversation_store
        self._runner_router = runner_router

    async def resolve_target(self, item: AgentQueueItem) -> DispatchTarget:
        if item.key.scope_id is None:
            raise DispatchFailed("manager item has no task scope")
        task = await asyncio.to_thread(self._task_store.get, item.key.scope_id)
        if task is None:
            raise DispatchFailed(f"task {item.key.scope_id} not found")
        if task.manager_conversation_id is None:
            raise DispatchFailed(f"task {item.key.scope_id} has no manager conversation")
        conv = await asyncio.to_thread(
            self._conversation_store.get_conversation,
            task.manager_conversation_id,
        )
        if conv is None:
            raise DispatchFailed(f"manager conversation {task.manager_conversation_id} missing")
        self._store.set_queue_conversation(item.key, task.manager_conversation_id)
        harness = conv.harness_override or "cursor-native"
        return DispatchTarget(
            session_id=task.manager_conversation_id,
            harness=harness,
        )

    async def deliver(self, item: AgentQueueItem, target: DispatchTarget) -> None:
        await _inject_notice(
            item,
            target,
            conversation_store=self._conversation_store,
            runner_router=self._runner_router,
        )


# A callable that launches/reconnects a session runner for a conversation.
# Injected so the handler does not depend on a live ``Request`` (the dispatcher
# runs outside request scope). ``None`` means runner ensure is unavailable.
EnsureRunner = Callable[[str], Awaitable[None]] | None


class WorkerDispatchHandler(RoleDispatchHandler):
    """Dispatch one task item to its worker slot.

    The queue's ``scope_id`` is the worker id. The gate measures the slot's
    *current* session — the previous item's conversation, from
    ``worker.target_id`` — because a worker dispatch creates a fresh
    conversation that is idle by definition. ``deliver`` creates that fresh
    conversation + execution, moves the task item to ``running``, caches the
    new conversation on the queue row (so the status feed can complete the
    in-flight item when the worker settles), and ensures the runner.

    A worker that *ran* and failed is handled by ``notify_worker_session_status``
    (item back to ``queued``, not redispatched) — the status feed clears the
    in-flight queue item so the next queued item can go out.
    """

    def __init__(
        self,
        store: AgentQueueStore,
        task_store: TaskStore,
        task_item_store: TaskItemStore,
        task_event_store: TaskEventStore,
        worker_store: WorkerStore,
        conversation_store: ConversationStore,
        agent_store: AgentStore,
        task_role_profile_store: TaskRoleProfileStore,
        runner_router: RunnerRouter | None,
        ensure_runner: EnsureRunner = None,
        session_creator: Any | None = None,
        app_state: Any | None = None,
    ) -> None:
        self._store = store
        self._task_store = task_store
        self._task_item_store = task_item_store
        self._task_event_store = task_event_store
        self._worker_store = worker_store
        self._conversation_store = conversation_store
        self._agent_store = agent_store
        self._task_role_profile_store = task_role_profile_store
        self._runner_router = runner_router
        self._ensure_runner = ensure_runner
        self._session_creator = session_creator
        self._app_state = app_state

    async def resolve_target(self, item: AgentQueueItem) -> DispatchTarget:
        if item.key.scope_id is None:
            raise DispatchFailed("worker item has no slot scope")
        worker = await asyncio.to_thread(
            self._worker_store.get_worker,
            item.key.scope_id,
        )
        if worker is None:
            raise DispatchFailed(f"worker slot {item.key.scope_id} not found")
        # Accepted proposals initialize on the worker queue so the HTTP request
        # stays fast and each worker slot serializes its own launch.
        if worker.target_id is None and worker.state in {
            "uninitialized",
            "initialization_failed",
        }:
            if self._session_creator is None or self._app_state is None:
                raise DispatchFailed("worker initialization is unavailable")
            claimed = await asyncio.to_thread(
                self._worker_store.claim_initialization,
                worker.id,
            )
            if claimed is not None:
                worker = await initialize_internal_worker(
                    claimed,
                    worker_store=self._worker_store,
                    session_creator=self._session_creator,
                    app_state=self._app_state,
                    user_id=(
                        None
                        if item.key.owner_user_id == "__anonymous__"
                        else item.key.owner_user_id
                    ),
                )
            else:
                worker = await asyncio.to_thread(self._worker_store.get_worker, worker.id)
                if worker is None:
                    raise DispatchFailed("worker disappeared during initialization")
        if worker.target_id is None and worker.state == "initializing":
            return DispatchTarget(session_id=None, ready=False)
        # The session-status gate remains the dispatch authority for internal
        # providers; the adapter mirrors the same observation onto worker.state.
        session_id = worker.target_id
        if session_id is None:
            reason = worker.failure_reason or f"worker {worker.id} has no initialized target"
            raise DispatchFailed(reason)
        harness: str | None = None
        if session_id is not None:
            conv = await asyncio.to_thread(
                self._conversation_store.get_conversation,
                session_id,
            )
            if conv is not None:
                harness = conv.harness_override
        return DispatchTarget(
            session_id=session_id,
            harness=harness,
            ready=worker.state == "idle" and not worker.needs_response,
        )

    async def deliver(self, item: AgentQueueItem, target: DispatchTarget) -> None:
        from omnigent.agent_tasks.dispatch import parse_dispatch_payload

        _ = target  # the gate already evaluated against the slot's prior session
        if item.key.scope_id is None or not item.source_ids:
            raise DispatchFailed("worker item has no slot scope or source item")
        worker = await asyncio.to_thread(
            self._worker_store.get_worker,
            item.key.scope_id,
        )
        if worker is None:
            raise DispatchFailed(f"worker slot {item.key.scope_id} not found at deliver time")
        task = await asyncio.to_thread(self._task_store.get, worker.task_id)
        if task is None:
            raise DispatchFailed(f"task {worker.task_id} not found")
        task_item = await asyncio.to_thread(self._task_item_store.get_item, item.source_ids[0])
        if task_item is None:
            raise DispatchFailed(f"task item {item.source_ids[0]} not found")

        payload = parse_dispatch_payload(item.payload)
        manager_role_profile = await asyncio.to_thread(
            load_manager_role_profile,
            self._task_role_profile_store,
            task,
        )

        def _opt_str(key: str) -> str | None:
            value = payload.get(key)
            return str(value) if value is not None else None

        task = await ensure_task_manager_for_dispatch(
            task=task,
            task_store=self._task_store,
            conversation_store=self._conversation_store,
            role_profile=manager_role_profile,
            host_id=_opt_str("host_id"),
            workspace=_opt_str("workspace"),
            harness=_opt_str("harness"),
            model=_opt_str("model"),
            session_creator=self._session_creator,
            app_state=self._app_state,
        )
        params = resolve_dispatch_params(
            payload=payload,
            role_profile=manager_role_profile,
            host_id=_opt_str("host_id"),
            workspace=_opt_str("workspace"),
            harness=_opt_str("harness"),
            model=_opt_str("model"),
        )

        if self._ensure_runner is not None and worker.target_id is not None:
            try:
                await self._ensure_runner(worker.target_id)
            except Exception as exc:
                raise DispatchFailed(
                    f"worker runner ensure failed for {worker.target_id}: {exc}"
                ) from exc

        _execution, worker_conv_id = await dispatch_worker_for_item(
            task=task,
            item=task_item,
            params=params,
            task_store=self._task_store,
            task_item_store=self._task_item_store,
            task_event_store=self._task_event_store,
            worker_store=self._worker_store,
            conversation_store=self._conversation_store,
            session_creator=self._session_creator,
            app_state=self._app_state,
            idempotency_key=item.id,
        )
        # Cache the conversation so the status feed can complete this item
        # when the worker session settles.
        self._store.set_queue_conversation(item.key, worker_conv_id)
        if self._runner_router is None:
            return
        conversation = await asyncio.to_thread(
            self._conversation_store.get_conversation,
            worker_conv_id,
        )
        if conversation is None:
            raise DispatchFailed(f"worker conversation {worker_conv_id} disappeared")
        messages = await asyncio.to_thread(
            self._conversation_store.list_items,
            worker_conv_id,
            limit=100,
            order="desc",
        )
        persisted = next(
            (message for message in messages.data if message.response_id == _execution.id),
            None,
        )
        if persisted is None:
            raise DispatchFailed(
                f"worker instruction for execution {_execution.id} was not persisted"
            )
        try:
            routed = self._runner_router.client_for_session_resources(
                worker_conv_id,
                conversation=conversation,
            )
            response = await routed.client.post(
                f"/v1/sessions/{worker_conv_id}/events",
                json={
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": params.instructions}],
                    "agent_id": conversation.agent_id,
                    "model": conversation.agent_id or "",
                    "persisted_item_id": persisted.id,
                },
                timeout=30.0,
            )
            response.raise_for_status()
        except Exception as exc:
            await asyncio.to_thread(
                complete_execution,
                self._task_event_store,
                _execution.id,
                status="failed",
                error=str(exc),
                error_code="dispatch_failed",
            )
            raise DispatchFailed(
                f"worker instruction delivery failed for {worker_conv_id}: {exc}"
            ) from exc

    async def on_parked(self, item: AgentQueueItem, state: str) -> None:
        """Mirror the park onto the task item the queue entry was carrying.

        Without this the board keeps showing the item as queued or running while
        its slot is halted — a stall with no explanation, which is the failure
        the queue control plane exists to make visible.
        """
        if not item.source_ids:
            return
        item_id = item.source_ids[0]

        def _park() -> None:
            updated = self._task_item_store.update_item(item_id, state=state)
            if updated is None:
                return
            task = self._task_store.get(updated.task_id)
            if task is not None:
                sync_task_activity_state(
                    task,
                    task_store=self._task_store,
                    task_item_store=self._task_item_store,
                )

        await asyncio.to_thread(_park)
