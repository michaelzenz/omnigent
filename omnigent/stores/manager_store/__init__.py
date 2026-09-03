"""First-class task manager persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from omnigent.entities import Manager

_UNSET: Any = object()


class ManagerStore(ABC):
    """Abstract store for managers keyed by conversation id."""

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    @abstractmethod
    def get(self, conversation_id: str) -> Manager | None:
        """Return a manager by conversation id."""

    @abstractmethod
    def list(self, *, owner_user_id: str | None) -> list[Manager]:
        """List one owner's managers in creation order."""

    @abstractmethod
    def upsert(
        self,
        conversation_id: str,
        *,
        owner_user_id: str | None,
        role_key: str,
        description: str,
    ) -> Manager:
        """Create a manager or update its mutable metadata."""

    @abstractmethod
    def update(
        self,
        conversation_id: str,
        *,
        owner_user_id: Any = _UNSET,
        role_key: str | None = None,
        description: str | None = None,
    ) -> Manager | None:
        """Update mutable manager fields, returning ``None`` when missing."""
