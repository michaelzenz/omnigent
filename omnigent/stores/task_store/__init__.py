"""Agent-task store — persists managed tasks and typed tags."""

from __future__ import annotations

from abc import ABC, abstractmethod
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
        manager_conversation_id: str | None = None,
        state: str = "idle",
        priority: int = 2,
        tags: list[TaskTag] | None = None,
    ) -> Task:
        """Insert a new managed task."""

    @abstractmethod
    def get(self, task_id: str) -> Task | None:
        """Return a task by id, or ``None`` if not found."""

    @abstractmethod
    def get_by_manager_conversation_id(self, conversation_id: str) -> Task | None:
        """Return the task whose manager session matches *conversation_id*."""

    @abstractmethod
    def list(
        self,
        *,
        state: str | None = None,
    ) -> list[Task]:
        """List tasks ordered by ``queue_rank DESC, id DESC``."""

    @abstractmethod
    def update(
        self,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        internal_note: str | None = None,
        manager_conversation_id: str | None = _UNSET,
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
    def get_tags(self, task_id: str) -> list[TaskTag]:
        """Return all tags for a task."""

    @abstractmethod
    def set_tags(self, task_id: str, tags: list[TaskTag]) -> list[TaskTag]:
        """Replace all tags on a task."""

    @abstractmethod
    def list_task_ids_by_tag(self, tag_type: str, tag: str) -> list[str]:
        """Return task ids with the given typed tag."""
