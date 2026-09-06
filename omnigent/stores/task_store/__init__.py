"""Agent-task store — persists managed tasks and typed tags."""

from __future__ import annotations

from abc import ABC, abstractmethod
from builtins import list as builtin_list
from typing import Any

from omnigent.entities import Task, TaskTag

_UNSET: Any = object()


class TaskStore(ABC):
    """Abstract base for managed-task persistence."""

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    @abstractmethod
    def create(
        self,
        task_id: str,
        title: str,
        goal: str,
        *,
        owner_user_id: str | None = None,
        manager_role_key: str | None = None,
        description: str | None = None,
        internal_note: str | None = None,
        manager_id: str | None = None,
        state: str = "idle",
        priority: int = 2,
        tags: list[TaskTag] | None = None,
    ) -> Task:
        """Insert a new managed task."""

    @abstractmethod
    def get(self, task_id: str) -> Task | None:
        """Return a task by id, or ``None`` if not found."""

    @abstractmethod
    def get_by_manager_id(self, manager_id: str) -> Task | None:
        """Return the task owned by the manager with the given durable id."""

    @abstractmethod
    def list(
        self,
        *,
        state: str | None = None,
    ) -> list[Task]:
        """List tasks ordered by ``queue_rank DESC, id DESC``."""

    @abstractmethod
    def list_recent(self, limit: int) -> builtin_list[Task]:
        """List the most recently touched tasks (``updated_at``, falling back to
        ``created_at``), newest first. No state filter — recency only."""

    @abstractmethod
    def list_by_manager_id(self, manager_id: str) -> builtin_list[Task]:
        """List every task bound to one manager."""

    @abstractmethod
    def list_manager_ids(self, *, owner_user_id: str | None = None) -> builtin_list[str]:
        """Distinct manager ids across live tasks, optionally per owner."""

    @abstractmethod
    def update(
        self,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        internal_note: str | None = None,
        manager_id: str | None = _UNSET,
        owner_user_id: str | None = _UNSET,
        manager_role_key: str | None = None,
        state: str | None = None,
        goal: str | None = None,
        priority: int | None = None,
    ) -> Task | None:
        """Update mutable task fields."""

    @abstractmethod
    def bump_queue_rank(self, task_id: str) -> Task | None:
        """Move a task to the front of the board queue."""

    @abstractmethod
    def move_to_queue_end(self, task_id: str) -> Task | None:
        """Move a task to the end of the board queue."""

    @abstractmethod
    def count_by_manager_role_key(
        self,
        manager_role_key: str,
        *,
        state: str | None = None,
    ) -> int:
        """Count tasks using a manager glossary role key."""

    @abstractmethod
    def delete(self, task_id: str) -> bool:
        """Delete a task and its tags/bindings. Idempotent."""

    @abstractmethod
    def get_tags(self, task_id: str) -> builtin_list[TaskTag]:
        """Return all tags for a task."""

    @abstractmethod
    def set_tags(self, task_id: str, tags: builtin_list[TaskTag]) -> builtin_list[TaskTag]:
        """Replace all tags on a task."""

    @abstractmethod
    def list_task_ids_by_tag(self, tag_type: str, tag: str) -> builtin_list[str]:
        """Return task ids with the given typed tag."""
