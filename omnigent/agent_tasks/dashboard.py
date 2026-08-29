"""Read models for managed task dashboard/card views."""

from __future__ import annotations

import json
from typing import Any, Literal

from omnigent.entities import Task, TaskAsset, TaskEventExecution, TaskItem, Worker
from omnigent.stores.task_asset_store import TaskAssetStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_item_store import TaskItemStore
from omnigent.stores.worker_store import WorkerStore

_RUNNING_EXECUTION_STATUSES = frozenset({"queued", "running"})
_TERMINAL_EXECUTION_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
_WORKER_LANE_ITEM_STATES = frozenset(
    {"draft", "pending", "queued", "running", "interrupted", "dispatch_failed"}
)
_WORKER_STATE = Literal["new", "active", "idle"]


def build_task_dashboard(
    task: Task,
    task_event_store: TaskEventStore,
    task_item_store: TaskItemStore,
    worker_store: WorkerStore,
    task_asset_store: TaskAssetStore | None = None,
) -> dict[str, Any]:
    """Build a card-shaped snapshot for one managed task."""
    items = task_item_store.list_items_for_task(task.id)
    inbox_items = [item for item in items if item.worker_id is None and item.state == "pending"]
    reconcile_queue = task_event_store.list_events(state="routed", task_id=task.id)
    executions = task_event_store.list_executions_for_task(task.id)
    item_by_id = {item.id: item for item in items}
    workers = worker_store.list_workers_for_task(task.id)
    # Exclude terminated workers — they're untracked but kept for audit.
    workers = [w for w in workers if w.state != "terminated"]
    worker_by_id = {worker.id: worker for worker in workers}

    worker_ids = set(worker_by_id)
    for item in items:
        if item.worker_id is not None:
            worker_ids.add(item.worker_id)
    for execution in executions:
        item = item_by_id.get(execution.task_item_id)
        if item is not None and item.worker_id is not None:
            worker_ids.add(item.worker_id)

    workers = [
        _worker_lane(
            worker_by_id[worker_id],
            items,
            executions,
            item_by_id,
        )
        for worker_id in sorted(worker_ids)
        if worker_id in worker_by_id
    ]
    workers.sort(key=_worker_lane_rank)

    has_running_workers = any(
        execution.status in _RUNNING_EXECUTION_STATUSES for execution in executions
    )
    assets = task_asset_store.list_assets_for_task(task.id) if task_asset_store is not None else []
    active_items = [item for item in items if item.state not in {"done", "cancelled"}]
    done_items = sorted(
        (item for item in items if item.state == "done"),
        key=lambda item: (item.updated_at or item.created_at, item.id),
        reverse=True,
    )
    recent_by_worker: dict[str, list[dict[str, Any]]] = {}
    for item in done_items:
        if item.worker_id is None:
            continue
        bucket = recent_by_worker.setdefault(item.worker_id, [])
        if len(bucket) < 3:
            bucket.append(_item_summary(item))

    return {
        "object": "agent.task.dashboard",
        "task": {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "goal": task.goal,
            "state": task.state,
            "created_at": task.created_at,
            "priority": task.priority,
            "queue_rank": task.queue_rank,
            "manager_conversation_id": task.manager_conversation_id,
        },
        "derived": {
            "has_running_workers": has_running_workers,
        },
        "active_items": [_item_summary(item) for item in active_items],
        "recent_done_items": {
            "all": [_item_summary(item) for item in done_items[:3]],
            "by_worker": recent_by_worker,
        },
        "inbox_items": [_item_summary(item) for item in inbox_items],
        "reconcile_queue_count": len(reconcile_queue),
        "assets": [_asset_summary(asset) for asset in assets],
        "workers": workers,
    }


def _worker_lane_rank(lane: dict[str, Any]) -> tuple[int, str]:
    order = {"active": 0, "new": 1, "idle": 2}
    return (order.get(lane["state"], 3), lane["worker_id"])


def _worker_lane(
    worker: Worker,
    items: list[TaskItem],
    executions: list[TaskEventExecution],
    item_by_id: dict[str, TaskItem],
) -> dict[str, Any]:
    worker_items = [
        item
        for item in items
        if item.worker_id == worker.id and item.state in _WORKER_LANE_ITEM_STATES
    ]
    worker_executions = [
        execution
        for execution in executions
        if item_by_id.get(execution.task_item_id) is not None
        and item_by_id[execution.task_item_id].worker_id == worker.id
    ]
    has_ever_executed = len(worker_executions) > 0
    covered_item_ids: set[str] = set()
    covered_execution_ids: set[str] = set()
    rows: list[dict[str, Any]] = []

    for execution in worker_executions:
        if execution.status in _TERMINAL_EXECUTION_STATUSES:
            continue
        item = item_by_id.get(execution.task_item_id)
        rows.append(
            _execution_row(
                execution,
                item,
                default_folded=False,
                sort_at=_execution_sort_at(execution),
            )
        )
        covered_item_ids.add(execution.task_item_id)
        covered_execution_ids.add(execution.id)

    for item in worker_items:
        if item.id in covered_item_ids:
            continue
        if item.state in {
            "draft",
            "pending",
            "queued",
            "running",
            "interrupted",
            "dispatch_failed",
        }:
            rows.append(
                _item_row(
                    item,
                    default_folded=False,
                    sort_at=_item_sort_at(item),
                )
            )
            covered_item_ids.add(item.id)

    for execution in worker_executions:
        if execution.id in covered_execution_ids:
            continue
        if execution.status in _TERMINAL_EXECUTION_STATUSES:
            item = item_by_id.get(execution.task_item_id)
            rows.append(
                _execution_row(
                    execution,
                    item,
                    default_folded=True,
                    sort_at=_execution_sort_at(execution),
                )
            )
            covered_execution_ids.add(execution.id)
            covered_item_ids.add(execution.task_item_id)

    rows.sort(key=lambda row: -int(row["sort_at"]))

    state, situation = _worker_state_and_situation(
        worker_items,
        worker_executions,
        item_by_id,
        has_ever_executed=has_ever_executed,
    )
    try:
        snapshot = json.loads(worker.provider_configuration or "{}")
    except (TypeError, ValueError):
        snapshot = {}
    launch = snapshot.get("launch") if isinstance(snapshot, dict) else None
    if not isinstance(launch, dict):
        launch = {}

    return {
        "worker_id": worker.id,
        "kind": worker.kind,
        "state": state,
        "worker_state": worker.state,
        "target_id": worker.target_id,
        "needs_response": worker.needs_response,
        "provider_name": worker.provider_name,
        "host_id": launch.get("host_id"),
        "workspace": launch.get("workspace"),
        "failure_reason": worker.failure_reason,
        "situation": situation,
        "rows": rows,
        "executions": [
            _execution_summary(execution, item_by_id.get(execution.task_item_id))
            for execution in worker_executions
        ],
    }


def _worker_state_and_situation(
    worker_items: list[TaskItem],
    worker_executions: list[TaskEventExecution],
    item_by_id: dict[str, TaskItem],
    *,
    has_ever_executed: bool,
) -> tuple[_WORKER_STATE, str]:
    running = next(
        (execution for execution in worker_executions if execution.status == "running"),
        None,
    )
    if running is not None:
        item = item_by_id.get(running.task_item_id)
        title = item.title if item is not None else "Work"
        return "active", f"Running: {title}"

    if not has_ever_executed:
        awaiting = sum(1 for item in worker_items if item.state in {"draft", "pending"})
        if awaiting:
            suffix = f" · {awaiting} awaiting" if awaiting > 1 else " · 1 awaiting"
            return "new", f"New{suffix}"
        return "new", "New"

    pending = sum(1 for item in worker_items if item.state in {"draft", "pending", "queued"})
    if pending:
        return "idle", f"Idle · {pending} pending"
    return "idle", "Idle"


def _item_sort_at(item: TaskItem) -> int:
    return item.updated_at or item.created_at


def _execution_sort_at(execution: TaskEventExecution) -> int:
    return execution.finished_at or execution.started_at or execution.assigned_at


def _item_row(item: TaskItem, *, default_folded: bool, sort_at: int) -> dict[str, Any]:
    return {
        "kind": "item",
        "item": _item_summary(item),
        "default_folded": default_folded,
        "sort_at": sort_at,
    }


def _execution_row(
    execution: TaskEventExecution,
    item: TaskItem | None,
    *,
    default_folded: bool,
    sort_at: int,
) -> dict[str, Any]:
    return {
        "kind": "execution",
        "execution": _execution_summary(execution, item),
        "default_folded": default_folded,
        "sort_at": sort_at,
    }


def _asset_summary(asset: TaskAsset) -> dict[str, Any]:
    return {
        "id": asset.id,
        "kind": asset.kind,
        "category": asset.category,
        "title": asset.title,
        "url": asset.url,
        "created_at": asset.created_at,
    }


def _item_summary(item: TaskItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "title": item.title,
        "description": item.description,
        "instructions": item.instructions,
        "internal_note": item.internal_note,
        "state": item.state,
        "worker_id": item.worker_id,
        "kind": item.kind,
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
        "event_title": item.title if item is not None else None,
        "item": _item_summary(item) if item is not None else None,
        "status": execution.status,
        "result_summary": execution.result_summary,
        "error": execution.error,
        "conversation_id": execution.conversation_id,
        "attempt_no": execution.attempt_no,
        "assigned_at": execution.assigned_at,
        "started_at": execution.started_at,
        "finished_at": execution.finished_at,
    }
