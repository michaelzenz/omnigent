"""Worker session completion hook for managed task executions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from omnigent.agent_tasks.executions import complete_execution
from omnigent.agent_tasks.task_activity import sync_task_activity_state
from omnigent.agent_tasks.wake import wake_task_manager_for_execution
from omnigent.entities import Task
from omnigent.runner.routing import RunnerRouter
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_item_store import TaskItemStore
from omnigent.stores.task_store import TaskStore
from omnigent.stores.worker_store import WORKER_KIND_MANAGED, WorkerStore

_logger = logging.getLogger(__name__)

TerminalStatus = Literal["idle", "failed"]


@dataclass
class TaskCompletionContext:
    """Stores required to handle worker completion notifications."""

    task_store: TaskStore
    task_event_store: TaskEventStore
    task_item_store: TaskItemStore
    conversation_store: ConversationStore
    worker_store: WorkerStore
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

    :returns: ``True`` when a managed worker session was handled.
    """
    if _context is None or status not in {"idle", "failed"}:
        return False
    worker = _context.worker_store.get_by_session_id(session_id)
    if worker is None or worker.kind != WORKER_KIND_MANAGED:
        return False
    execution = _context.task_event_store.get_execution_by_conversation_id(session_id)
    if execution is None:
        _logger.warning(
            "worker completion: managed worker without execution for session %s",
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

    item_state = "done" if terminal_status == "succeeded" else "queued"
    _context.task_item_store.update_item(execution.task_item_id, state=item_state)

    task = _context.task_store.get(worker.task_id)
    if task is not None:
        sync_task_activity_state(
            task,
            task_store=_context.task_store,
            task_item_store=_context.task_item_store,
        )
    if task is None or task.manager_conversation_id is None:
        return True
    await wake_task_manager_for_execution(
        manager_conversation_id=task.manager_conversation_id,
        execution=completed,
        event=None,
        conversation_store=_context.conversation_store,
        runner_router=_context.runner_router,
        task_item_store=_context.task_item_store,
    )
    return True
