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
    profile_id: str,
    worker_store: WorkerStore,
    task_item_store: TaskItemStore,
) -> tuple[TaskItem, Worker]:
    """Create a worker slot for an item and bind it."""
    stripped = profile_id.strip()
    if not stripped:
        raise OmnigentError("profile_id must be non-empty", code=ErrorCode.INVALID_INPUT)
    if item.worker_id is not None:
        existing = worker_store.get_worker(item.worker_id)
        if existing is not None and existing.profile_id == stripped:
            return item, existing
    worker = worker_store.create_worker(
        _generate_worker_id(),
        item.task_id,
        stripped,
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
