"""GlobalHub delivery queue persistence.

Each item is a message for one role session. Reads and writes are scoped to
the item's owner, except the dispatcher's claim, which scans every owner and
keeps each item's owner on it.
"""

from __future__ import annotations

import builtins
from abc import ABC, abstractmethod
from collections.abc import Collection, Mapping
from typing import Any

from omnigent.entities import AgentQueueItem


class AgentQueueStore(ABC):
    """Abstract base for GlobalHub delivery queue persistence."""

    def __init__(self, storage_location: str) -> None:
        """
        :param storage_location: Backend-specific storage URI, e.g.
            ``"sqlite:///chat.db"`` for SQLAlchemy.
        """
        self.storage_location = storage_location

    @abstractmethod
    def enqueue(
        self,
        item_id: str,
        *,
        owner_user_id: str | None,
        target_session_id: str,
        kind: str,
        payload: Mapping[str, Any] | None = None,
        event_ids: Collection[str] = (),
    ) -> AgentQueueItem | None:
        """
        Queue a message for a session; the given events then point at it, all or none.

        :param item_id: Pre-generated id (bare 32-char hex).
        :param owner_user_id: The owner, or ``None`` in single-user mode.
        :param target_session_id: The session the message goes to.
        :param kind: ``"events"``, ``"notice_click"`` or ``"spawn_managers"``.
        :param payload: Inputs for the message text.
        :param event_ids: The owner's task events the item carries.
        :returns: The queued item, or ``None``, changing nothing, when an event
            is missing, someone else's, or already in an item.
        """

    @abstractmethod
    def get(self, item_id: str, *, owner_user_id: str | None) -> AgentQueueItem | None:
        """Return the owner's item, or ``None`` when missing or someone else's."""

    @abstractmethod
    def list(
        self,
        *,
        owner_user_id: str | None,
        states: Collection[str] | None = None,
        target_session_id: str | None = None,
    ) -> builtins.list[AgentQueueItem]:
        """List the owner's items in arrival order."""

    @abstractmethod
    def claim_due(self, *, now: int, limit: int = 100) -> builtins.list[AgentQueueItem]:
        """
        Claim the items ready to send, for the dispatcher, and mark them ``sending``.

        An item is ready when it is queued, its retry time has passed, it is
        its session's oldest queued item, and no item to that session is
        being sent or waiting for its turn to end.

        :param now: Current Unix epoch seconds.
        :param limit: At most this many items.
        :returns: The claimed items in arrival order, at most one per session.
        """

    @abstractmethod
    def release_stale_claims(self, *, claimed_before: int) -> int:
        """
        Put back every owner's items claimed before ``claimed_before`` and never settled.

        A dispatcher that crashed mid-send leaves its claim behind; putting it
        back lets the item be sent again.

        :returns: How many items went back to the queue.
        """

    @abstractmethod
    def list_sent(self) -> builtins.list[AgentQueueItem]:
        """List every owner's ``sent`` items in arrival order, for the dispatcher."""

    @abstractmethod
    def mark_done_for_session(self, session_id: str) -> int:
        """
        Record that the turn handling the item sent to ``session_id`` ended.

        :returns: How many items became ``done``.
        """

    @abstractmethod
    def defer(
        self, item_id: str, *, owner_user_id: str | None, not_before: int
    ) -> AgentQueueItem | None:
        """
        Put a claimed item back without counting a failed attempt, e.g. while its session is busy.

        :param not_before: Earliest Unix epoch second for the next send.
        :returns: The queued item, or ``None`` when it is not the owner's item
            in ``sending``.
        """

    @abstractmethod
    def mark_sent(self, item_id: str, *, owner_user_id: str | None) -> AgentQueueItem | None:
        """
        Record that a claimed item's send succeeded.

        :returns: The ``sent`` item, or ``None`` when it is not the owner's
            item in ``sending``.
        """

    @abstractmethod
    def mark_done(self, item_id: str, *, owner_user_id: str | None) -> AgentQueueItem | None:
        """
        Record that the turn that handled a sent item ended.

        :returns: The ``done`` item, or ``None`` when it is not the owner's
            item in ``sent``.
        """

    @abstractmethod
    def retry_later(
        self,
        item_id: str,
        *,
        owner_user_id: str | None,
        error: str,
        not_before: int,
    ) -> AgentQueueItem | None:
        """
        Put a claimed item back in the queue after a failed send.

        :param error: Why the send failed.
        :param not_before: Earliest Unix epoch second for the next send.
        :returns: The queued item with one more attempt, or ``None`` when it
            is not the owner's item in ``sending``.
        """

    @abstractmethod
    def delete(self, item_id: str, *, owner_user_id: str | None) -> bool:
        """
        Delete the owner's item and release its events, so they can be queued again.

        :returns: Whether the item existed.
        """
