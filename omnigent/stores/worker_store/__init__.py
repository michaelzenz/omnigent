"""Worker slot persistence for managed tasks."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from omnigent.entities import Worker

_UNSET: Any = object()


class WorkerStore(ABC):
    """Abstract base for worker persistence."""

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    @abstractmethod
    def create_worker(
        self,
        worker_id: str,
        task_id: str,
        profile_id: str,
        *,
        session_id: str | None = None,
    ) -> Worker:
        """Insert a worker slot for one task."""

    @abstractmethod
    def get_worker(self, worker_id: str) -> Worker | None:
        """Return one worker by id."""

    @abstractmethod
    def list_workers_for_task(self, task_id: str) -> list[Worker]:
        """List workers for a task ordered by created_at asc, id asc."""

    @abstractmethod
    def update_worker(
        self,
        worker_id: str,
        *,
        session_id: str | None = _UNSET,
        profile_id: str | None = None,
    ) -> Worker | None:
        """Update mutable worker fields."""
