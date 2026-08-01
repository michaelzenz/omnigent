"""Resolve managed tasks from conversation session ids."""

from __future__ import annotations

from omnigent.entities import Task
from omnigent.stores.task_store import TaskStore
from omnigent.stores.worker_store import WorkerStore


def task_for_session(
    session_id: str,
    *,
    task_store: TaskStore,
    worker_store: WorkerStore,
) -> Task | None:
    """Return the task bound to a worker or manager session, if any."""
    worker = worker_store.get_by_session_id(session_id)
    if worker is not None:
        return task_store.get(worker.task_id)
    return task_store.get_by_manager_conversation_id(session_id)
