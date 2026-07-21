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
        manager_agent_id: str,
        title: str,
        *,
        owner_user_id: str | None = None,
        description: str | None = None,
        charter: str | None = None,
        manager_conversation_id: str | None = None,
        state: str = "active",
        tags: list[TaskTag] | None = None,
    ) -> Task:
        """Insert a new managed task."""

    @abstractmethod
    def get(self, task_id: str) -> Task | None:
        """Return a task by id, or ``None`` if not found."""

    @abstractmethod
    def list(
        self,
        *,
        state: str | None = None,
        manager_agent_id: str | None = None,
    ) -> list[Task]:
        """List tasks ordered by ``updated_at DESC, id DESC``."""

    @abstractmethod
    def update(
        self,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        charter: str | None = None,
        manager_agent_id: str | None = None,
        manager_conversation_id: str | None = _UNSET,
        owner_user_id: str | None = _UNSET,
        state: str | None = None,
    ) -> Task | None:
        """Update mutable task fields."""

    @abstractmethod
    def delete(self, task_id: str) -> bool:
        """Delete a task and its tags/bindings. Idempotent."""

    @abstractmethod
    def get_tags(self, task_id: str) -> list[TaskTag]:
        """Return all tags for a task."""

    @abstractmethod
    def set_tags(self, task_id: str, tags: list[TaskTag]) -> list[TaskTag]:
        """Replace all tags on a task and refresh ``search_text``."""

    @abstractmethod
    def list_task_ids_by_tag(self, tag_type: str, tag: str) -> list[str]:
        """Return task ids with the given typed tag."""

    @abstractmethod
    def rebuild_search_text(self, task_id: str) -> Task | None:
        """Rebuild ``search_text`` from title, charter, and tags."""

    @abstractmethod
    def search(self, query: str, *, limit: int = 20) -> list[Task]:
        """Case-insensitive substring search over ``search_text``."""
