"""First-class task manager persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from omnigent.entities import Manager

_UNSET: Any = object()


class ManagerStore(ABC):
    """Abstract store for managers keyed by durable manager id.

    A manager row is self-describing: it carries the execution snapshot
    (host, workspace, harness, model, agent/prompt profiles) captured at
    spawn, so session re-creation reads only the row.
    """

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    @abstractmethod
    def get(self, manager_id: str) -> Manager | None:
        """Return a manager by durable id."""

    @abstractmethod
    def get_by_conversation_id(self, conversation_id: str) -> Manager | None:
        """Return the manager currently bound to a session, if any."""

    @abstractmethod
    def list(self, *, owner_user_id: str | None) -> list[Manager]:
        """List one owner's managers in creation order."""

    @abstractmethod
    def upsert(
        self,
        manager_id: str,
        *,
        owner_user_id: str | None,
        role_key: str,
        description: str,
        conversation_id: str | None,
        title: str | None = None,
        host_id: str | None = None,
        workspace: str | None = None,
        harness: str | None = None,
        model: str | None = None,
        agent_profile_id: str | None = None,
        prompt_profile_id: str | None = None,
    ) -> Manager:
        """Create a manager or update its mutable metadata."""

    @abstractmethod
    def update(
        self,
        manager_id: str,
        *,
        owner_user_id: Any = _UNSET,
        role_key: str | None = None,
        description: str | None = None,
        conversation_id: Any = _UNSET,
        title: Any = _UNSET,
        host_id: Any = _UNSET,
        workspace: Any = _UNSET,
        harness: Any = _UNSET,
        model: Any = _UNSET,
        agent_profile_id: Any = _UNSET,
        prompt_profile_id: Any = _UNSET,
    ) -> Manager | None:
        """Update mutable manager fields, returning ``None`` when missing."""

    @abstractmethod
    def delete(self, manager_id: str) -> bool:
        """Remove a manager row, e.g. when its registry entry is retired."""
