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
        canonical_key: str | None = None,
        instructions: str | None = None,
        worker_agent_id: str | None = None,
        model: str | None = None,
        host_id: str | None = None,
        workspace: str | None = None,
        harness: str | None = None,
        priority: int = 0,
        created_by: str = "manager",
        routing_proposal: str | None = None,
    ) -> TaskItem:
        """Insert a new task item."""

    @abstractmethod
    def get_item(self, item_id: str) -> TaskItem | None:
        """Return one task item by id."""

    @abstractmethod
    def get_open_routing_item_by_canonical_key(
        self,
        canonical_key: str,
    ) -> TaskItem | None:
        """Return the open secretary routing proposal for a canonical key."""

    @abstractmethod
    def list_items_by_state(
        self,
        state: str,
        *,
        created_by: str | None = None,
    ) -> list[TaskItem]:
        """List task items in one state, newest first."""

    @abstractmethod
    def get_routing_item_for_event(self, event_id: str) -> TaskItem | None:
        """Return an open routing-proposed item linked to an event, if any."""

    @abstractmethod
    def get_item_by_canonical_key(
        self,
        task_id: str,
        canonical_key: str,
    ) -> TaskItem | None:
        """Return the newest open item with a canonical key on one task."""

    @abstractmethod
    def list_items_for_task(
        self,
        task_id: str,
        *,
        state: str | None = None,
    ) -> list[TaskItem]:
        """List task items ordered by priority desc, created_at asc."""

    @abstractmethod
    def update_item(
        self,
        item_id: str,
        *,
        title: str | None = None,
        state: str | None = None,
        canonical_key: str | None = _UNSET,
        instructions: str | None = _UNSET,
        worker_agent_id: str | None = _UNSET,
        model: str | None = _UNSET,
        host_id: str | None = _UNSET,
        workspace: str | None = _UNSET,
        harness: str | None = _UNSET,
        priority: int | None = None,
        task_id: str | None = None,
        routing_proposal: str | None = _UNSET,
    ) -> TaskItem | None:
        """Update mutable task-item fields."""

    @abstractmethod
    def link_event(
        self,
        task_item_id: str,
        event_id: str,
        *,
        relation: str = "triggered",
    ) -> TaskItemEvent:
        """Associate a task event with a task item."""

    @abstractmethod
    def list_events_for_item(self, task_item_id: str) -> list[TaskItemEvent]:
        """List event links for one task item."""

    @abstractmethod
    def create_fyi_cluster(
        self,
        cluster_id: str,
        owner_user_id: str,
        headline: str,
        *,
        canonical_key: str | None = None,
        rationale: str | None = None,
        state: str = "awaiting_user_ack",
    ) -> FyiCluster:
        """Insert a secretary FYI cluster."""

    @abstractmethod
    def get_fyi_cluster(self, cluster_id: str) -> FyiCluster | None:
        """Return one FYI cluster by id."""

    @abstractmethod
    def get_open_fyi_cluster_by_canonical_key(
        self,
        canonical_key: str,
    ) -> FyiCluster | None:
        """Return the open FYI cluster for a canonical key."""

    @abstractmethod
    def get_fyi_cluster_for_event(self, event_id: str) -> FyiCluster | None:
        """Return an open FYI cluster linked to an event, if any."""

    @abstractmethod
    def list_fyi_clusters(
        self,
        *,
        owner_user_id: str | None = None,
        state: str | None = None,
    ) -> list[FyiCluster]:
        """List FYI clusters newest first."""

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
        """Update an FYI cluster."""

    @abstractmethod
    def link_fyi_cluster_event(self, cluster_id: str, event_id: str) -> None:
        """Attach an event to an FYI cluster."""

    @abstractmethod
    def list_fyi_cluster_event_ids(self, cluster_id: str) -> list[str]:
        """Return event ids included in an FYI cluster."""
