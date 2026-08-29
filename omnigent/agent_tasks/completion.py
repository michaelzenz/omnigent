"""Worker session completion hook for managed task executions."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Literal

from omnigent.agent_tasks.event_types import WORKER_EXECUTION_FINISHED_EVENT_TYPE
from omnigent.agent_tasks.executions import complete_execution
from omnigent.agent_tasks.notices import _format_worker_notice
from omnigent.agent_tasks.task_activity import sync_task_activity_state
from omnigent.db.utils import now_epoch
from omnigent.entities import Task, TaskEventExecution
from omnigent.entities.agent_queue import AgentQueueKey
from omnigent.runner.routing import RunnerRouter
from omnigent.stores.agent_queue_store import AgentQueueStore
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
    agent_queue_store: AgentQueueStore | None = None
    runner_router: RunnerRouter | None = None


_context: TaskCompletionContext | None = None


def configure_task_completion(context: TaskCompletionContext | None) -> None:
    """Register or clear the global worker-completion handler."""
    global _context
    _context = context


def get_task_completion_context() -> TaskCompletionContext | None:
    """Return the configured worker-completion handler context."""
    return _context


async def observe_worker_session_status(
    session_id: str,
    status: str,
    *,
    needs_response: bool = False,
    failure_reason: str | None = None,
) -> bool:
    """Mirror target activity onto the durable Worker without completing a dispatch."""
    if _context is None:
        return False
    worker = _context.worker_store.get_by_target_id(session_id)
    if worker is None or worker.kind != WORKER_KIND_MANAGED:
        return False
    if status == "idle":
        state = "idle"
    elif status == "failed":
        state = "disconnected"
    else:
        state = "busy"
    _context.worker_store.update_worker(
        worker.id,
        state=state,
        needs_response=needs_response,
        failure_reason=failure_reason if status == "failed" else None,
        last_observed_at=now_epoch(),
    )
    return True


async def notify_worker_session_status(
    session_id: str,
    status: TerminalStatus,
    *,
    output: str | None = None,
) -> bool:
    """
    Update task execution state and emit a manager-facing event when a worker
    session settles.

    A ``worker.execution.finished`` event is created pre-routed to the task.
    When an ``agent_queue_store`` is configured, a notice is enqueued directly
    onto the manager's agent queue so the manager is woken without waiting for
    the packager's poll cycle. The event is then marked ``reconciled`` so the
    packager does not duplicate the work.

    The emission is best-effort: a failure is logged and never blocks the
    execution and item state updates that already succeeded above.

    :returns: ``True`` when a managed worker session was handled.
    """
    if _context is None or status not in {"idle", "failed"}:
        return False
    worker = _context.worker_store.get_by_target_id(session_id)
    if worker is None or worker.kind != WORKER_KIND_MANAGED:
        return False
    execution = _context.task_event_store.get_execution_by_conversation_id(session_id)
    if execution is None:
        _logger.warning(
            "worker completion: managed worker without execution for session %s",
            session_id,
        )
        return False
    return await notify_worker_execution_status(execution.id, status, output=output)


async def notify_worker_execution_status(
    execution_id: str,
    status: TerminalStatus,
    *,
    output: str | None = None,
) -> bool:
    """Complete one exact managed-worker execution."""
    if _context is None or status not in {"idle", "failed"}:
        return False
    execution = _context.task_event_store.get_execution(execution_id)
    if execution is None or execution.conversation_id is None:
        return False
    worker = _context.worker_store.get_by_target_id(execution.conversation_id)
    if worker is None or worker.kind != WORKER_KIND_MANAGED:
        return False
    if execution.status in {"succeeded", "failed", "cancelled"}:
        return True
    summary = (output or "").strip() or None
    await observe_worker_session_status(
        execution.conversation_id,
        status,
        failure_reason=summary if status == "failed" else None,
    )
    terminal_status = "succeeded" if status == "idle" else "failed"
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
    if _context.agent_queue_store is not None and execution.agent_queue_item_id is not None:
        queue_item = _context.agent_queue_store.get_item(
            execution.agent_queue_item_id,
        )
        if queue_item is not None:
            _context.agent_queue_store.complete_inflight(
                queue_item.key,
                item_id=queue_item.id,
                now=now_epoch(),
            )

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
    await _emit_worker_execution_finished_event(
        task=task,
        execution=completed,
        output=output,
    )
    return True


async def _emit_worker_execution_finished_event(
    *,
    task: Task,
    execution: TaskEventExecution,
    output: str | None = None,
) -> None:
    """Create a pre-routed event and directly enqueue a manager notice.

    Born ``routed`` to the task with the owner attributed. When an
    ``agent_queue_store`` is available, a notice is enqueued directly onto the
    manager queue and the event is immediately marked ``reconciled`` so the
    packager skips it. ``source_key`` is the execution id, so a duplicate
    completion edge cannot double-emit.
    """
    assert _context is not None
    item = _context.task_item_store.get_item(execution.task_item_id)
    item_title = item.title if item is not None else execution.task_item_id
    item_instructions = item.instructions if item is not None else None
    payload = json.dumps(
        {
            "execution_id": execution.id,
            "status": execution.status,
            "task_item_id": execution.task_item_id,
            "item_title": item_title,
            "instructions": item_instructions,
            "result_summary": execution.result_summary,
            "error": execution.error,
            "output": output,
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
        return

    if _context.agent_queue_store is not None and task.manager_conversation_id is not None:
        notice = _format_worker_notice(event)
        try:
            _context.agent_queue_store.enqueue(
                uuid.uuid4().hex,
                AgentQueueKey(
                    role="manager",
                    owner_user_id=owner,
                    scope_id=task.manager_conversation_id,
                ),
                "notice",
                source_ids=[event.id],
                payload=notice,
            )
            _context.task_event_store.update_event(
                event.id,
                state="reconciled",
                processed_at=now_epoch(),
            )
        except Exception:  # noqa: BLE001
            _logger.warning(
                "failed to enqueue manager notice for event %s; "
                "packager will pick it up on next poll",
                event.id,
                exc_info=True,
            )
