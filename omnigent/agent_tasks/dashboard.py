"""Read models for managed task dashboard/card views."""

from __future__ import annotations

from typing import Any

from omnigent.agent_tasks.constants import MANAGER_TRIAGE_EVENT_STATES
from omnigent.agent_tasks.event_types import MANAGER_PROPOSAL, is_manager_internal_event
from omnigent.entities import Task, TaskEvent, TaskEventExecution
from omnigent.stores.task_event_store import TaskEventStore

_RUNNING_EXECUTION_STATUSES = frozenset({"queued", "running"})


def build_task_dashboard(
    task: Task,
    task_event_store: TaskEventStore,
) -> dict[str, Any]:
    """Build a card-shaped snapshot for one managed task."""
    events = task_event_store.list_events(task_id=task.id)
    executions = task_event_store.list_executions_for_task(task.id)
    event_by_id = {event.id: event for event in events}

    pending_proposals = [
        _event_summary(event)
        for event in events
        if event.event_type == MANAGER_PROPOSAL and event.state == "awaiting_user_ack"
    ]
    pending_inbound = [
        _event_summary(event)
        for event in events
        if event.state in MANAGER_TRIAGE_EVENT_STATES and not is_manager_internal_event(event.event_type)
    ]

    workers: dict[str, list[dict[str, Any]]] = {}
    for execution in executions:
        workers.setdefault(execution.worker_agent_id, []).append(
            _execution_summary(execution, event_by_id.get(execution.event_id))
        )

    has_running_workers = any(
        execution.status in _RUNNING_EXECUTION_STATUSES for execution in executions
    )

    return {
        "object": "agent.task.dashboard",
        "task": {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "state": task.state,
            "manager_conversation_id": task.manager_conversation_id,
        },
        "derived": {
            "has_running_workers": has_running_workers,
        },
        "pending_proposals": pending_proposals,
        "pending_inbound_events": pending_inbound,
        "workers": [
            {
                "worker_agent_id": worker_agent_id,
                "executions": rows,
            }
            for worker_agent_id, rows in sorted(workers.items())
        ],
    }


def _event_summary(event: TaskEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "event_type": event.event_type,
        "title": event.title,
        "summary": event.summary,
        "state": event.state,
        "payload": event.payload,
        "created_at": event.created_at,
        "updated_at": event.updated_at,
    }


def _execution_summary(
    execution: TaskEventExecution,
    event: TaskEvent | None,
) -> dict[str, Any]:
    return {
        "id": execution.id,
        "event_id": execution.event_id,
        "event_title": event.title if event is not None else None,
        "status": execution.status,
        "result_summary": execution.result_summary,
        "error": execution.error,
        "conversation_id": execution.conversation_id,
        "attempt_no": execution.attempt_no,
        "assigned_at": execution.assigned_at,
        "started_at": execution.started_at,
        "finished_at": execution.finished_at,
    }
