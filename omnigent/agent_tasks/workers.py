"""Worker slot helpers for managed task items."""

from __future__ import annotations

import uuid

from omnigent.entities import TaskItem, Worker
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.task_item_store import TaskItemStore
from omnigent.stores.worker_store import WorkerStore


def _generate_worker_id() -> str:
    return uuid.uuid4().hex


def assign_worker_profile(
    *,
    item: TaskItem,
    role_key: str,
    worker_store: WorkerStore,
    task_item_store: TaskItemStore,
) -> tuple[TaskItem, Worker]:
    """Bind an item's worker lane to a role, creating the lane when needed."""
    stripped = role_key.strip()
    if not stripped:
        raise OmnigentError("role_key must be non-empty", code=ErrorCode.INVALID_INPUT)
    if item.worker_id is not None:
        existing = worker_store.get_worker(item.worker_id)
        if existing is not None:
            if existing.role_key == stripped:
                return item, existing
            # A lane that has not run yet can be re-pointed at another role;
            # once it holds a session the history belongs to the old role.
            if existing.session_id is None:
                rebound = worker_store.update_worker(existing.id, role_key=stripped)
                if rebound is None:
                    raise OmnigentError("Worker not found", code=ErrorCode.NOT_FOUND)
                return item, rebound
    worker = worker_store.create_worker(
        _generate_worker_id(),
        item.task_id,
        role_key=stripped,
    )
    updated = task_item_store.update_item(item.id, worker_id=worker.id)
    if updated is None:
        raise OmnigentError("Task item not found", code=ErrorCode.NOT_FOUND)
    return updated, worker


def worker_for_item(
    item: TaskItem,
    *,
    worker_store: WorkerStore,
) -> Worker | None:
    """Return the worker slot assigned to an item, if any."""
    if item.worker_id is None:
        return None
    return worker_store.get_worker(item.worker_id)
