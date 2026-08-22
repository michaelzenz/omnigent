"""Worker lookup helpers for managed task items."""

from __future__ import annotations

import uuid

from omnigent.entities import TaskItem, Worker
from omnigent.stores.worker_store import WorkerStore


def _generate_worker_id() -> str:
    """Return a durable PuppyGarden Worker ID."""
    return uuid.uuid4().hex


def worker_for_item(item: TaskItem, *, worker_store: WorkerStore) -> Worker | None:
    """Return the durable Worker assigned to an item, if any."""
    if item.worker_id is None:
        return None
    return worker_store.get_worker(item.worker_id)
