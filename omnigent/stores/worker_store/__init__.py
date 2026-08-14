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
        role_key: str | None = None,
        agent_profile_id: str | None = None,
        session_id: str | None = None,
        external_session_hint: str | None = None,
    ) -> Worker:
        """Insert a worker slot for one task."""

    @abstractmethod
    def get_worker(self, worker_id: str) -> Worker | None:
        """Return one worker by id."""

    @abstractmethod
    def get_by_session_id(self, session_id: str) -> Worker | None:
        """Return the worker row for a live session, if any."""

    @abstractmethod
    def get_by_external_hint(self, external_session_hint: str) -> Worker | None:
        """Return the external worker matching a watcher-provided session hint."""

    @abstractmethod
    def list_workers_for_task(self, task_id: str) -> list[Worker]:
        """List workers for a task ordered by created_at asc, id asc."""

    @abstractmethod
    def update_worker(
        self,
        worker_id: str,
        *,
        session_id: str | None = _UNSET,
        role_key: str | None = None,
        agent_profile_id: str | None = None,
        kind: str | None = None,
    ) -> Worker | None:
        """Update mutable worker fields."""
