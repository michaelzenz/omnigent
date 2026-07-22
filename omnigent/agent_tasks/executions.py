"""Task worker execution lifecycle helpers."""

from __future__ import annotations

import uuid

from omnigent.db.utils import now_epoch
from omnigent.entities import Task, TaskEvent, TaskEventExecution
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.task_event_store import TaskEventStore

_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


def _generate_execution_id() -> str:
    return uuid.uuid4().hex


def next_attempt_no(task_event_store: TaskEventStore, event_id: str) -> int:
    """Return the next monotonic attempt number for an event."""
    existing = task_event_store.list_executions_for_event(event_id)
    if not existing:
        return 1
    return max(row.attempt_no for row in existing) + 1


def start_execution(
    *,
    task: Task,
    event: TaskEvent,
    worker_agent_id: str,
    task_event_store: TaskEventStore,
    conversation_id: str | None = None,
    status: str = "queued",
) -> TaskEventExecution:
    """Record a new worker execution attempt for a task event."""
    if event.task_id is not None and event.task_id != task.id:
        raise OmnigentError(
            "Event does not belong to task",
            code=ErrorCode.INVALID_INPUT,
        )
    attempt_no = next_attempt_no(task_event_store, event.id)
    now = now_epoch()
    execution = task_event_store.create_execution(
        _generate_execution_id(),
        event.id,
        task.id,
        task.manager_agent_id,
        worker_agent_id,
        status=status,
        attempt_no=attempt_no,
        conversation_id=conversation_id,
        assigned_at=now,
    )
    if conversation_id is not None and status == "running":
        task_event_store.update_execution(
            execution.id,
            started_at=now,
        )
        execution = task_event_store.get_execution(execution.id)
        assert execution is not None
    return execution


def mark_execution_running(
    task_event_store: TaskEventStore,
    execution_id: str,
    *,
    conversation_id: str | None = None,
) -> TaskEventExecution | None:
    """Transition an execution to running."""
    now = now_epoch()
    return task_event_store.update_execution(
        execution_id,
        status="running",
        conversation_id=conversation_id,
        started_at=now,
    )


def complete_execution(
    task_event_store: TaskEventStore,
    execution_id: str,
    *,
    status: str,
    result_summary: str | None = None,
    error: str | None = None,
    error_code: str | None = None,
) -> TaskEventExecution | None:
    """Mark an execution terminal. Idempotent when already terminal."""
    if status not in _TERMINAL_STATUSES:
        raise OmnigentError(
            f"status must be one of: {', '.join(sorted(_TERMINAL_STATUSES))}",
            code=ErrorCode.INVALID_INPUT,
        )
    existing = task_event_store.get_execution(execution_id)
    if existing is None:
        return None
    if existing.status in _TERMINAL_STATUSES:
        return existing
    return task_event_store.update_execution(
        execution_id,
        status=status,
        finished_at=now_epoch(),
        result_summary=result_summary,
        error=error,
        error_code=error_code,
    )
