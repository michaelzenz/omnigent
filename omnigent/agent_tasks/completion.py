"""Worker session completion hook for managed task executions."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Literal

from omnigent.agent_tasks.event_types import WORKER_EXECUTION_FINISHED_EVENT_TYPE
from omnigent.agent_tasks.executions import complete_execution
from omnigent.agent_tasks.task_activity import sync_task_activity_state
from omnigent.db.utils import now_epoch
from omnigent.entities import Task, TaskEventExecution
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
    Update task execution state and emit a manager-facing event when a worker
    session settles.

    The manager is no longer woken directly. Instead a ``worker.execution.finished``
    event is created pre-routed to the task, and the manager packager picks it up
    on its next poll — the same durable pipe every other routed event uses. The
    emission is best-effort: a failure is logged and never blocks the execution
    and item state updates that already succeeded above.

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
    if task is None:
        return True
    _emit_worker_execution_finished_event(task=task, execution=completed)
    return True


def _emit_worker_execution_finished_event(
    *,
    task: Task,
    execution: TaskEventExecution,
) -> None:
    """Create a pre-routed event so the manager packager notices the worker settled.

    Born ``routed`` to the task with the owner attributed, so the manager packager
    polls it like any other routed event. ``source_key`` is the execution id, so a
    duplicate completion edge cannot double-emit.
    """
    assert _context is not None
    item_title = execution.task_item_id
    item = _context.task_item_store.get_item(execution.task_item_id)
    if item is not None:
        item_title = item.title
    payload = json.dumps(
        {
            "execution_id": execution.id,
            "status": execution.status,
            "task_item_id": execution.task_item_id,
            "item_title": item_title,
            "result_summary": execution.result_summary,
            "error": execution.error,
        }
    )
    title = f"Worker execution {execution.status} for item {item_title}"
    owner = task.owner_user_id or "__anonymous__"
    try:
        event = _context.task_event_store.create_event(
            uuid.uuid4().hex,
            WORKER_EXECUTION_FINISHED_EVENT_TYPE,
            title,
            task_id=task.id,
            source="worker",
            source_key=execution.id,
            state="routed",
            payload=payload,
            owner_user_id=owner,
        )
        _context.task_event_store.update_event(
            event.id,
            routed_at=now_epoch(),
        )
    except Exception:
        _logger.exception(
            "failed to emit %s event for execution %s on task %s",
            WORKER_EXECUTION_FINISHED_EVENT_TYPE,
            execution.id,
            task.id,
        )
