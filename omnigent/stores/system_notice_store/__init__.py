"""GlobalHub system notice persistence.

Every read and write is scoped to the notice's owner: another user's notice
reads as missing. Each owner has at most one active notice.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any

from omnigent.entities import SystemNotice


class SystemNoticeStore(ABC):
    """Abstract base for GlobalHub system notice persistence."""

    def __init__(self, storage_location: str) -> None:
        """
        :param storage_location: Backend-specific storage URI, e.g.
            ``"sqlite:///chat.db"`` for SQLAlchemy.
        """
        self.storage_location = storage_location

    @abstractmethod
    def publish(
        self,
        notice_id: str,
        *,
        owner_user_id: str | None,
        title: str,
        body_kind: str = "markdown",
        body: str | None = None,
        actions: Sequence[Mapping[str, Any]] = (),
        publisher_session_id: str | None = None,
        publisher_label: str | None = None,
        changes: Sequence[Mapping[str, Any]] = (),
    ) -> SystemNotice:
        """
        Make a new notice the owner's active one, resolving the one it replaces.

        :param notice_id: Pre-generated id (bare 32-char hex).
        :param owner_user_id: The owner, or ``None`` in single-user mode.
        :param title: The notice's headline.
        :param body_kind: ``"markdown"`` or ``"html"``.
        :param body: The notice's body.
        :param actions: The declared actions the user can click.
        :param publisher_session_id: The session that published it, when confirmed.
        :param publisher_label: That session's title.
        :param changes: The staged changes the notice proposes, if any.
        :returns: The new active notice.
        """

    @abstractmethod
    def get_active(self, *, owner_user_id: str | None) -> SystemNotice | None:
        """Return the owner's active notice, or ``None`` when there is none."""

    @abstractmethod
    def resolve_active(self, *, owner_user_id: str | None) -> SystemNotice | None:
        """Resolve the owner's active notice; return it, or ``None`` when there is none."""
