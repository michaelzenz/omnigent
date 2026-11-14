"""GlobalHub worker persistence.

A worker wraps one session for one task. Every read and write is scoped to the
worker's owner: another user's worker reads as missing.
"""

from __future__ import annotations

import builtins
from abc import ABC, abstractmethod
from collections.abc import Collection
from typing import Any

from omnigent.entities import Worker

#: Sentinel for "leave unchanged" where ``None`` is itself a value to store.
UNSET: Any = object()


class WorkerStore(ABC):
    """Abstract base for GlobalHub worker persistence."""

    def __init__(self, storage_location: str) -> None:
        """
        :param storage_location: Backend-specific storage URI, e.g.
            ``"sqlite:///chat.db"`` for SQLAlchemy.
        """
        self.storage_location = storage_location

    @abstractmethod
    def create(
        self,
        worker_id: str,
        *,
        owner_user_id: str | None,
        task_id: str,
        session_id: str,
        title: str | None = None,
    ) -> Worker:
        """
        Attach a session to a task.

        :param worker_id: Pre-generated id (bare 32-char hex).
        :param owner_user_id: The task's owner, or ``None`` in single-user mode.
        :param task_id: The task the session works on.
        :param session_id: The session to attach.
        :param title: Short label for the session's recent work on the task.
        :returns: The new worker.
        :raises IntegrityError: When the session already has a worker on the task.
        """

    @abstractmethod
    def get(self, worker_id: str, *, owner_user_id: str | None) -> Worker | None:
        """Return the owner's worker, or ``None`` when missing or someone else's."""

    @abstractmethod
    def get_for_task_and_session(
        self, task_id: str, session_id: str, *, owner_user_id: str | None
    ) -> Worker | None:
        """Return the owner's worker for ``session_id`` on ``task_id``, or ``None``."""

    @abstractmethod
    def list_for_tasks(
        self, task_ids: Collection[str], *, owner_user_id: str | None
    ) -> builtins.list[Worker]:
        """List the owner's workers on these tasks, by task and then attach order."""

    @abstractmethod
    def list_for_session(
        self, session_id: str, *, owner_user_id: str | None
    ) -> builtins.list[Worker]:
        """List the owner's workers that wrap ``session_id``, in attach order."""

    @abstractmethod
    def update(
        self,
        worker_id: str,
        *,
        owner_user_id: str | None,
        title: str | None = UNSET,
    ) -> Worker | None:
        """
        Retitle the owner's worker.

        ``title`` takes ``None`` as a value and uses :data:`UNSET` to stay as it
        is. A change stamps ``updated_at``.

        :returns: The updated worker, or ``None`` when missing or someone else's.
        """

    @abstractmethod
    def delete(self, worker_id: str, *, owner_user_id: str | None) -> bool:
        """Detach the owner's worker from its task; return whether it existed."""
