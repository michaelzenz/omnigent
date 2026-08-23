"""Task asset persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod

from omnigent.entities import TaskAsset


class TaskAssetStore(ABC):
    """Abstract base for task asset persistence."""

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    @abstractmethod
    def create_asset(
        self,
        task_id: str,
        *,
        kind: str,
        category: str = "other",
        title: str,
        url: str | None = None,
    ) -> TaskAsset:
        """Insert a task asset."""

    @abstractmethod
    def list_assets_for_task(self, task_id: str) -> list[TaskAsset]:
        """List assets for one task ordered by id."""

    @abstractmethod
    def delete_asset(self, task_id: str, asset_id: int) -> bool:
        """Delete one asset from a task. Return True if a row was removed."""
