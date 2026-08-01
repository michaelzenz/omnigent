"""Per-role dispatch handlers — turn an :class:`AgentQueueItem` into a delivery.

The dispatcher knows *when* to send (gate, lease); a handler knows *how*. Each role
gets one handler registered under its role name in :class:`DispatcherContext.handlers`.
"""

from __future__ import annotations

import asyncio
import logging

from omnigent.agent_tasks.agent_builtins import TASK_SECRETARY_ROLE
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

_logger = logging.getLogger(__name__)


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
        from omnigent.server.routes.sessions import _wake_parent_for_blocked_child

        if not item.payload:
            raise DispatchFailed("secretary item has no payload to deliver")
        conv = await asyncio.to_thread(
            self._conversation_store.get_conversation,
            target.session_id,
        )
        if conv is None:
            raise DispatchFailed(
                f"secretary conversation {target.session_id} missing at deliver time"
            )
        ok = await _wake_parent_for_blocked_child(
            target.session_id,
            conv,
            item.payload,
            conversation_store=self._conversation_store,
            runner_router=self._runner_router,
        )
        if not ok:
            raise DispatchFailed(f"secretary wake delivery to {target.session_id} returned false")


async def asyncio_to_thread(func, *args):
    """Run a blocking store call off the event loop."""
    import asyncio

    return await asyncio.to_thread(func, *args)
