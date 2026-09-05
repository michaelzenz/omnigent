"""Resolve managed tasks from conversation session ids."""

from __future__ import annotations

from omnigent.entities import Task
from omnigent.stores.manager_store import ManagerStore
from omnigent.stores.task_store import TaskStore
from omnigent.stores.worker_store import WorkerStore


def task_for_session(
    session_id: str,
    *,
    task_store: TaskStore,
    worker_store: WorkerStore,
    manager_store: ManagerStore | None = None,
) -> Task | None:
    """Return the task bound to a worker or manager session, if any.

    A worker session resolves through ``worker.target_id``; a manager
    session resolves through the manager row's ``conversation_id`` pointer.
    """
    worker = worker_store.get_by_target_id(session_id)
    if worker is not None:
        return task_store.get(worker.task_id)
    if manager_store is not None:
        manager = manager_store.get_by_conversation_id(session_id)
        if manager is not None:
            return task_store.get_by_manager_id(manager.id)
    return None
