"""Read models for managed task dashboard/card views."""

from __future__ import annotations

from typing import Any

from omnigent.entities import Task, TaskEventExecution, TaskItem
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_item_store import TaskItemStore

_RUNNING_EXECUTION_STATUSES = frozenset({"queued", "running"})


def build_task_dashboard(
    task: Task,
    task_event_store: TaskEventStore,
    task_item_store: TaskItemStore,
) -> dict[str, Any]:
    """Build a card-shaped snapshot for one managed task."""
    items = task_item_store.list_items_for_task(task.id)
    inbox_items = [item for item in items if item.state == "awaiting_user_ack"]
    reconcile_queue = task_event_store.list_events(state="routed", task_id=task.id)
    executions = task_event_store.list_executions_for_task(task.id)
    item_by_id = {item.id: item for item in items}

    workers: dict[str, list[dict[str, Any]]] = {}
    for execution in executions:
        item = item_by_id.get(execution.task_item_id)
        workers.setdefault(execution.worker_agent_id, []).append(
            _execution_summary(execution, item),
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
        "inbox_items": [_item_summary(item) for item in inbox_items],
        "reconcile_queue_count": len(reconcile_queue),
        "workers": [
            {
                "worker_agent_id": worker_agent_id,
                "executions": rows,
            }
            for worker_agent_id, rows in sorted(workers.items())
        ],
    }


def _item_summary(item: TaskItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "title": item.title,
        "instructions": item.instructions,
        "state": item.state,
        "worker_agent_id": item.worker_agent_id,
        "model": item.model,
        "host_id": item.host_id,
        "workspace": item.workspace,
        "harness": item.harness,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _execution_summary(
    execution: TaskEventExecution,
    item: TaskItem | None,
) -> dict[str, Any]:
    return {
        "id": execution.id,
        "task_item_id": execution.task_item_id,
        "event_id": execution.event_id,
        "event_title": item.title if item is not None else None,
        "status": execution.status,
        "result_summary": execution.result_summary,
        "error": execution.error,
        "conversation_id": execution.conversation_id,
        "attempt_no": execution.attempt_no,
        "assigned_at": execution.assigned_at,
        "started_at": execution.started_at,
        "finished_at": execution.finished_at,
    }
