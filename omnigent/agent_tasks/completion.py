"""Worker session completion hook for managed task executions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from omnigent.agent_tasks.executions import complete_execution
from omnigent.agent_tasks.wake import wake_task_manager_for_execution
from omnigent.entities import Task
from omnigent.runner.routing import RunnerRouter
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_store import TaskStore

_logger = logging.getLogger(__name__)

TerminalStatus = Literal["idle", "failed"]


@dataclass
class TaskCompletionContext:
    """Stores required to handle worker completion notifications."""

    task_store: TaskStore
    task_event_store: TaskEventStore
    conversation_store: ConversationStore
    runner_router: RunnerRouter | None = None


_context: TaskCompletionContext | None = None


def configure_task_completion(context: TaskCompletionContext | None) -> None:
    """Register or clear the global worker-completion handler."""
    global _context
    _context = context


def get_task_completion_context() -> TaskCompletionContext | None:
    """Return the configured worker-completion handler context."""
    return _context


async def notify_worker_session_status(
    session_id: str,
    status: TerminalStatus,
    *,
    output: str | None = None,
) -> bool:
    """
    Update task execution state and wake the manager when a worker session settles.

    :returns: ``True`` when a task worker binding was handled.
    """
    if _context is None or status not in {"idle", "failed"}:
        return False
    binding = _context.task_event_store.get_binding(session_id)
    if binding is None or binding.binding_kind != "worker":
        return False
    execution = _context.task_event_store.get_execution_by_conversation_id(session_id)
    if execution is None:
        _logger.warning(
            "worker completion: binding without execution for session %s",
            session_id,
        )
        return False
    terminal_status = "succeeded" if status == "idle" else "failed"
    summary = (output or "").strip() or None
    completed = complete_execution(
        _context.task_event_store,
        execution.id,
        status=terminal_status,
        result_summary=summary if terminal_status == "succeeded" else None,
        error=summary if terminal_status == "failed" else None,
        error_code="worker_failed" if terminal_status == "failed" else None,
    )
    if completed is None:
        return True
    task = _context.task_store.get(binding.task_id)
    if task is None or task.manager_conversation_id is None:
        return True
    event = _context.task_event_store.get_event(execution.event_id)
    await wake_task_manager_for_execution(
        manager_conversation_id=task.manager_conversation_id,
        execution=completed,
        event=event,
        conversation_store=_context.conversation_store,
        runner_router=_context.runner_router,
    )
    return True
