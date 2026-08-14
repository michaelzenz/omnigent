"""Per-user session bindings for singleton task roles."""

from __future__ import annotations

from abc import ABC, abstractmethod

from omnigent.entities.task_role_profile import UserRoleSession


class UserRoleSessionStore(ABC):
    """Abstract base for per-user role session persistence."""

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    @abstractmethod
    def get(self, user_id: str, role: str) -> UserRoleSession | None:
        """Return the session binding for ``user_id`` and ``role``."""

    @abstractmethod
    def list_for_user(self, user_id: str) -> list[UserRoleSession]:
        """List every session binding owned by ``user_id``."""

    @abstractmethod
    def set_conversation(
        self,
        user_id: str,
        role: str,
        conversation_id: str | None,
    ) -> UserRoleSession:
        """Bind ``role`` to a conversation, or clear it by passing ``None``."""

    @abstractmethod
    def delete(self, user_id: str, role: str) -> bool:
        """Delete a session binding. Returns ``False`` when missing."""
