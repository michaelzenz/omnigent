"""Per-role dispatch handlers — turn an :class:`AgentQueueItem` into a delivery.

The dispatcher knows *when* to send (gate, lease); a handler knows *how*. Each role
gets one handler registered under its role name in :class:`DispatcherContext.handlers`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from omnigent.agent_tasks.agent_builtins import (
    TASK_SECRETARY_ROLE,
)
from omnigent.agent_tasks.dispatch import (
    dispatch_worker_for_item,
    resolve_dispatch_params,
)
from omnigent.agent_tasks.items import ensure_task_manager_for_dispatch
from omnigent.agent_tasks.queue.dispatcher import (
    DispatchFailed,
    DispatchTarget,
    RoleDispatchHandler,
)
from omnigent.entities import AgentQueueItem, Task
from omnigent.runner.routing import RunnerRouter
from omnigent.stores.agent_queue_store import AgentQueueStore
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_item_store import TaskItemStore
from omnigent.stores.task_role_profile_store import TaskRoleProfileStore
from omnigent.stores.task_store import TaskStore
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

    Shared by every role whose payload is a ``[System: …]`` notice — secretary and
    manager today. Raises :class:`DispatchFailed` when the payload is empty, the
    conversation is gone, or the runner refuses the injection.
    """
    from omnigent.server.routes.sessions import _wake_parent_for_blocked_child

    if not item.payload:
        raise DispatchFailed("notice item has no payload to deliver")
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
    )
    if not ok:
        raise DispatchFailed(f"notice delivery to {target.session_id} returned false")


class SecretaryDispatchHandler(RoleDispatchHandler):
    """Deliver secretary notices to the user's live secretary session.

    The target is the secretary's bound conversation from the role profile. The
    handler caches it on the queue row via :meth:`set_queue_conversation` so the
    status feed can reverse-look-up the queue from the session id when the
    secretary goes idle.
    """

    def __init__(
        self,
        store: AgentQueueStore,
        task_role_profile_store: TaskRoleProfileStore,
        conversation_store: ConversationStore,
        runner_router: RunnerRouter | None,
    ) -> None:
        self._store = store
        self._task_role_profile_store = task_role_profile_store
        self._conversation_store = conversation_store
        self._runner_router = runner_router

    async def resolve_target(self, item: AgentQueueItem) -> DispatchTarget:
        profile = self._task_role_profile_store.get(
            item.key.owner_user_id,
            TASK_SECRETARY_ROLE,
        )
        if profile is None or profile.conversation_id is None:
            raise DispatchFailed(f"no live secretary for user {item.key.owner_user_id}")
        conv = await asyncio.to_thread(
            self._conversation_store.get_conversation,
            profile.conversation_id,
        )
        if conv is None:
            raise DispatchFailed(f"secretary conversation {profile.conversation_id} missing")
        # Cache the target so the status feed can find this queue from the
        # session id alone when the secretary goes idle.
        self._store.set_queue_conversation(item.key, profile.conversation_id)
        harness = conv.harness_override or "claude-native"
        return DispatchTarget(
            session_id=profile.conversation_id,
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
    reverse look-up, the same way the secretary handler does.
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
EnsureRunner = "callable[[str], Awaitable[None]] | None"


class WorkerDispatchHandler(RoleDispatchHandler):
    """Dispatch one task item to its worker slot.

    The queue's ``scope_id`` is the worker id. The gate measures the slot's
    *current* session — the previous item's conversation, from
    ``worker.session_id`` — because a worker dispatch creates a fresh
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
        task_role_profile_store: TaskRoleProfileStore | None,
        runner_router: RunnerRouter | None,
        ensure_runner: EnsureRunner = None,
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

    async def resolve_target(self, item: AgentQueueItem) -> DispatchTarget:
        if item.key.scope_id is None:
            raise DispatchFailed("worker item has no slot scope")
        worker = await asyncio.to_thread(
            self._worker_store.get_worker,
            item.key.scope_id,
        )
        if worker is None:
            raise DispatchFailed(f"worker slot {item.key.scope_id} not found")
        # The slot's current session is the previous item's conversation; a fresh
        # slot has none, which the gate treats as immediately dispatchable.
        session_id = worker.session_id
        harness: str | None = None
        if session_id is not None:
            conv = await asyncio.to_thread(
                self._conversation_store.get_conversation,
                session_id,
            )
            if conv is not None:
                harness = conv.harness_override
        return DispatchTarget(session_id=session_id, harness=harness)

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
        role_profile = None
        if self._task_role_profile_store is not None:
            role_profile = await asyncio.to_thread(
                self._task_role_profile_store.get,
                item.key.owner_user_id,
                TASK_SECRETARY_ROLE,
            )

        def _opt_str(key: str) -> str | None:
            value = payload.get(key)
            return str(value) if value is not None else None

        def _bootstrap() -> Task:
            return ensure_task_manager_for_dispatch(
                task=task,
                task_store=self._task_store,
                task_event_store=self._task_event_store,
                conversation_store=self._conversation_store,
                agent_store=self._agent_store,
                role_profile=role_profile,
                host_id=_opt_str("host_id"),
                workspace=_opt_str("workspace"),
                harness=_opt_str("harness"),
                model=_opt_str("model"),
            )

        task = await asyncio.to_thread(_bootstrap)
        params = resolve_dispatch_params(
            payload={**payload, "worker_profile_id": worker.profile_id},
            role_profile=role_profile,
            host_id=_opt_str("host_id"),
            workspace=_opt_str("workspace"),
            harness=_opt_str("harness"),
            model=_opt_str("model"),
        )

        def _dispatch() -> tuple[Any, str]:
            return dispatch_worker_for_item(
                task=task,
                item=task_item,
                params=params,
                task_store=self._task_store,
                task_item_store=self._task_item_store,
                task_event_store=self._task_event_store,
                worker_store=self._worker_store,
                conversation_store=self._conversation_store,
            )

        _execution, worker_conv_id = await asyncio.to_thread(_dispatch)
        # Cache the new conversation so the status feed can complete this item
        # when the worker session settles.
        self._store.set_queue_conversation(item.key, worker_conv_id)
        if self._ensure_runner is not None:
            try:
                await self._ensure_runner(worker_conv_id)
            except Exception:
                _logger.exception(
                    "worker dispatch: runner ensure failed for %s; the worker "
                    "session was created but may not be live",
                    worker_conv_id,
                )
