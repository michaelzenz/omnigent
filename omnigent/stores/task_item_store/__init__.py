"""Task item and FYI cluster persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from omnigent.entities import FyiCluster, TaskItem, TaskItemEvent

_UNSET: Any = object()


class TaskItemStore(ABC):
    """Abstract base for task-item and FYI-cluster persistence."""

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    @abstractmethod
    def create_item(
        self,
        item_id: str,
        task_id: str,
        title: str,
        *,
        state: str = "draft",
        description: str | None = None,
        instructions: str | None = None,
        internal_note: str | None = None,
        worker_id: str | None = None,
        created_by: str = "manager",
    ) -> TaskItem:
        """Insert a new task item."""

    @abstractmethod
    def get_item(self, item_id: str) -> TaskItem | None:
        """Return one task item by id."""

    @abstractmethod
    def list_items_by_state(
        self,
        state: str,
        *,
        created_by: str | None = None,
    ) -> list[TaskItem]:
        """List task items in one state."""

    @abstractmethod
    def get_item_for_event(self, event_id: str) -> TaskItem | None:
        """Return the newest task item linked to an event."""

    @abstractmethod
    def get_event_ids_claimed_by_items(self, event_ids: list[str]) -> set[str]:
        """Subset of ``event_ids`` already linked to any task item."""

    @abstractmethod
    def list_items_for_task(
        self,
        task_id: str,
        *,
        state: str | None = None,
    ) -> list[TaskItem]:
        """List task items ordered by created_at asc."""

    @abstractmethod
    def update_item(
        self,
        item_id: str,
        *,
        title: str | None = None,
        state: str | None = None,
        instructions: str | None = _UNSET,
        description: str | None = _UNSET,
        internal_note: str | None = _UNSET,
        worker_id: str | None = _UNSET,
        task_id: str | None = None,
    ) -> TaskItem | None:
        """Update mutable task item fields."""

    @abstractmethod
    def link_event(
        self,
        task_item_id: str,
        event_id: str,
        *,
        relation: str = "triggered",
    ) -> TaskItemEvent:
        """Link a task item to a contributing event."""

    @abstractmethod
    def list_events_for_item(self, task_item_id: str) -> list[TaskItemEvent]:
        """List events linked to a task item."""

    @abstractmethod
    def create_fyi_cluster(
        self,
        cluster_id: str,
        owner_user_id: str,
        headline: str,
        *,
        rationale: str | None = None,
        state: str = "awaiting_user_ack",
    ) -> FyiCluster:
        """Insert an FYI cluster."""

    @abstractmethod
    def get_fyi_cluster(self, cluster_id: str) -> FyiCluster | None:
        """Return one FYI cluster by id."""

    @abstractmethod
    def get_fyi_cluster_for_event(self, event_id: str) -> FyiCluster | None:
        """Return the open FYI cluster for an event, if any."""

    @abstractmethod
    def get_event_ids_claimed_by_fyi_clusters(self, event_ids: list[str]) -> set[str]:
        """Subset of ``event_ids`` linked to an open FYI cluster."""

    @abstractmethod
    def list_fyi_clusters(
        self,
        *,
        owner_user_id: str | None = None,
        state: str | None = None,
    ) -> list[FyiCluster]:
        """List FYI clusters."""

    @abstractmethod
    def update_fyi_cluster(
        self,
        cluster_id: str,
        *,
        state: str | None = None,
        headline: str | None = None,
        rationale: str | None = None,
        resolved_at: int | None = None,
    ) -> FyiCluster | None:
        """Update one FYI cluster."""

    @abstractmethod
    def link_fyi_cluster_event(self, cluster_id: str, event_id: str) -> None:
        """Link an event to an FYI cluster."""

    @abstractmethod
    def list_fyi_cluster_event_ids(self, cluster_id: str) -> list[str]:
        """List event ids linked to an FYI cluster."""
