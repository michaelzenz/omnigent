"""GlobalHub manager persistence.

Every read and write is scoped to the manager's owner: another user's manager
reads as missing.
"""

from __future__ import annotations

import builtins
from abc import ABC, abstractmethod
from typing import Any

from omnigent.entities import Manager

#: Sentinel for "leave unchanged" where ``None`` is itself a value to store.
UNSET: Any = object()


class ManagerStore(ABC):
    """Abstract base for GlobalHub manager persistence."""

    def __init__(self, storage_location: str) -> None:
        """
        :param storage_location: Backend-specific storage URI, e.g.
            ``"sqlite:///chat.db"`` for SQLAlchemy.
        """
        self.storage_location = storage_location

    @abstractmethod
    def create(
        self,
        manager_id: str,
        *,
        owner_user_id: str | None,
        title: str,
        description: str = "",
        conversation_id: str | None = None,
        host_id: str | None = None,
        workspace: str | None = None,
    ) -> Manager:
        """
        Insert a manager.

        :param manager_id: Pre-generated id (bare 32-char hex).
        :param owner_user_id: Owning user, or ``None`` in single-user mode.
        :param title: Title of the manager's session.
        :param description: The scope the manager owns.
        :param conversation_id: The manager's session, when it already exists.
        :param host_id: Host override, or ``None`` for the owner's GlobalHub host.
        :param workspace: Workspace override, or ``None`` for the owner's
            GlobalHub workspace.
        :returns: The new manager.
        """

    @abstractmethod
    def get(self, manager_id: str, *, owner_user_id: str | None) -> Manager | None:
        """Return the owner's manager, or ``None`` when missing or someone else's."""

    @abstractmethod
    def get_by_conversation_id(
        self, conversation_id: str, *, owner_user_id: str | None
    ) -> Manager | None:
        """Return the owner's manager whose session is ``conversation_id``, or ``None``."""

    @abstractmethod
    def list(self, *, owner_user_id: str | None) -> builtins.list[Manager]:
        """List the owner's managers in creation order."""

    @abstractmethod
    def update(
        self,
        manager_id: str,
        *,
        owner_user_id: str | None,
        title: str | None = None,
        description: str | None = None,
        conversation_id: str | None = UNSET,
        host_id: str | None = UNSET,
        workspace: str | None = UNSET,
    ) -> Manager | None:
        """
        Change the given fields of the owner's manager.

        ``None`` leaves ``title`` and ``description`` as they are. The other
        fields take ``None`` as a value and use :data:`UNSET` to stay as they
        are. A change stamps ``updated_at``.

        :returns: The updated manager, or ``None`` when missing or someone else's.
        """

    @abstractmethod
    def delete(self, manager_id: str, *, owner_user_id: str | None) -> bool:
        """Delete the owner's manager; return whether it existed.

        Its tasks are unassigned and its session follows no source any more. The
        events routed to its session that it did not handle leave their queue
        item and wait for the broker again, with their delivery count started
        again.
        """
