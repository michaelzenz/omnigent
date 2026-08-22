"""Worker slot persistence for managed tasks."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from omnigent.entities import Worker

_UNSET: Any = object()

WORKER_KIND_MANAGED = "managed"
WORKER_KIND_EXTERNAL = "external"


class WorkerStore(ABC):
    """Abstract base for worker persistence."""

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    @abstractmethod
    def create_worker(
        self,
        worker_id: str,
        task_id: str,
        *,
        kind: str = WORKER_KIND_MANAGED,
        target_id: str | None = None,
        state: str = "uninitialized",
        needs_response: bool = False,
        provider_name: str | None = None,
        provider_configuration: str | None = None,
    ) -> Worker:
        """Insert a worker slot for one task."""

    @abstractmethod
    def get_worker(self, worker_id: str) -> Worker | None:
        """Return one worker by id."""

    @abstractmethod
    def get_by_target_id(self, target_id: str) -> Worker | None:
        """Return the Worker bound to a target session, if any."""

    @abstractmethod
    def list_workers_for_task(self, task_id: str) -> list[Worker]:
        """List workers for a task ordered by created_at asc, id asc."""

    @abstractmethod
    def update_worker(
        self,
        worker_id: str,
        *,
        kind: str | None = None,
        target_id: str | None = _UNSET,
        state: str | None = None,
        needs_response: bool | None = None,
        failure_reason: str | None = _UNSET,
        last_observed_at: int | None = _UNSET,
    ) -> Worker | None:
        """Update mutable worker fields."""
