"""Per-role dispatch handlers — turn an :class:`AgentQueueItem` into a delivery.

The dispatcher knows *when* to send (gate, lease); a handler knows *how*. Each role
gets one handler registered under its role name in :class:`DispatcherContext.handlers`.
"""

from __future__ import annotations

import asyncio
import logging

from omnigent.agent_tasks.agent_builtins import (
    TASK_SECRETARY_ROLE,
)
from omnigent.agent_tasks.queue.dispatcher import (
    DispatchFailed,
    DispatchTarget,
    RoleDispatchHandler,
)
from omnigent.entities import AgentQueueItem
from omnigent.runner.routing import RunnerRouter
from omnigent.stores.agent_queue_store import AgentQueueStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_role_profile_store import TaskRoleProfileStore
from omnigent.stores.task_store import TaskStore

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
