"""Managed-task activity state derived from running worker items."""

from __future__ import annotations

from omnigent.entities import Task
from omnigent.stores.task_item_store import TaskItemStore
from omnigent.stores.task_store import TaskStore

_PROTECTED_TASK_STATES = frozenset({"pending", "archived"})


def sync_task_activity_state(
    task: Task,
    *,
    task_store: TaskStore,
    task_item_store: TaskItemStore,
) -> Task:
    """Set task state to active while any item is running, otherwise idle."""
    if task.state in _PROTECTED_TASK_STATES:
        return task
    items = task_item_store.list_items_for_task(task.id)
    target = "active" if any(item.state == "running" for item in items) else "idle"
    if task.state == target:
        return task
    updated = task_store.update(task.id, state=target)
    return updated if updated is not None else task
