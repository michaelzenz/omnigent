"""Best-effort wake delivery for managed task managers."""

from __future__ import annotations

import logging

from omnigent.entities import TaskEvent, TaskEventExecution
from omnigent.runner.routing import RunnerRouter
from omnigent.server.routes.sessions import _wake_parent_for_blocked_child
from omnigent.stores.conversation_store import ConversationStore

_logger = logging.getLogger(__name__)


def _format_event_notice(event: TaskEvent) -> str:
    summary = event.summary or ""
    summary_block = f"\n{summary}" if summary else ""
    return (
        f"[System: task event {event.id} routed to this manager] "
        f"{event.title}{summary_block}"
    )


async def wake_task_manager_for_execution(
    *,
    manager_conversation_id: str,
    execution: TaskEventExecution,
    event: TaskEvent | None,
    conversation_store: ConversationStore,
    runner_router: RunnerRouter | None,
) -> bool:
    """Wake the task manager when a worker execution reaches a terminal state."""
    conv = conversation_store.get_conversation(manager_conversation_id)
    if conv is None:
        _logger.warning(
            "task manager wake skipped: conversation %s missing for execution %s",
            manager_conversation_id,
            execution.id,
        )
        return False
    event_title = event.title if event is not None else execution.event_id
    summary = execution.result_summary or execution.error or ""
    summary_block = f"\n{summary}" if summary else ""
    notice = (
        f"[System: worker execution {execution.id} {execution.status} "
        f"for event {event_title}]{summary_block}"
    )
    return await _wake_parent_for_blocked_child(
        manager_conversation_id,
        conv,
        notice,
        conversation_store=conversation_store,
        runner_router=runner_router,
    )


async def wake_task_manager_for_event(
    *,
    manager_conversation_id: str,
    event: TaskEvent,
    conversation_store: ConversationStore,
    runner_router: RunnerRouter | None,
) -> bool:
    """
    Inject a synthetic user message into the manager session.

    Best-effort: transport failures are logged and reported as ``False``.
    """
    conv = conversation_store.get_conversation(manager_conversation_id)
    if conv is None:
        _logger.warning(
            "task manager wake skipped: conversation %s missing for event %s",
            manager_conversation_id,
            event.id,
        )
        return False
    return await _wake_parent_for_blocked_child(
        manager_conversation_id,
        conv,
        _format_event_notice(event),
        conversation_store=conversation_store,
        runner_router=runner_router,
    )
