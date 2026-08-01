"""Per-user task agent role profile persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod

from omnigent.entities.task_role_profile import UserTaskRoleProfile


class TaskRoleProfileStore(ABC):
    """Abstract base for per-user task role profile persistence."""

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    @abstractmethod
    def get(self, user_id: str, role: str) -> UserTaskRoleProfile | None:
        """Return the profile for ``user_id`` and ``role``, or ``None`` if unset."""

    @abstractmethod
    def upsert(
        self,
        user_id: str,
        role: str,
        *,
        agent_profile_id: str | None = None,
        conversation_id: str | None = None,
        harness: str | None = None,
        model: str | None = None,
        host_id: str | None = None,
        workspace: str | None = None,
        clear_conversation_id: bool = False,
    ) -> UserTaskRoleProfile:
        """Create or update a task role profile."""
