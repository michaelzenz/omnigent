"""Timer item persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from omnigent.entities import TimerItem


class TimerItemStore(ABC):
    """Abstract base for deferred timer item persistence."""

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    @abstractmethod
    def create_item(
        self,
        item_id: str,
        task_type: str,
        fire_at: int,
        host_id: str,
        payload: dict[str, Any],
        *,
        owner_user_id: str | None = None,
    ) -> TimerItem:
        """Insert a new pending timer item."""

    @abstractmethod
    def get_item(self, item_id: str) -> TimerItem | None:
        """Return one timer item by id."""

    @abstractmethod
    def list_due(self, host_id: str, *, now: int) -> list[TimerItem]:
        """List pending items due on *host_id*, oldest first."""

    @abstractmethod
    def claim_item(self, item_id: str, host_id: str) -> TimerItem | None:
        """Transition pending → running when *host_id* matches."""

    @abstractmethod
    def complete_item(self, item_id: str, host_id: str) -> TimerItem | None:
        """Transition running → done when *host_id* matches."""

    @abstractmethod
    def fail_item(self, item_id: str, host_id: str) -> TimerItem | None:
        """Transition running → failed when *host_id* matches."""
