"""GlobalHub task event persistence.

Every read and write is scoped to the event's owner: another user's event
reads as missing.
"""

from __future__ import annotations

import builtins
from abc import ABC, abstractmethod
from collections.abc import Collection, Sequence
from typing import Any

from omnigent.entities import EventTag, TaskEvent


class TaskEventStore(ABC):
    """Abstract base for GlobalHub task event persistence."""

    def __init__(self, storage_location: str) -> None:
        """
        :param storage_location: Backend-specific storage URI, e.g.
            ``"sqlite:///chat.db"`` for SQLAlchemy.
        """
        self.storage_location = storage_location

    @abstractmethod
    def create(
        self,
        event_id: str,
        *,
        owner_user_id: str | None,
        event_type: str,
        title: str,
        payload: dict[str, Any] | None = None,
        source: str | None = None,
        source_key: str | None = None,
        source_offset: str | None = None,
        source_session_id: str | None = None,
        tags: Sequence[EventTag] = (),
        task_id: str | None = None,
        manager_id: str | None = None,
    ) -> TaskEvent:
        """
        Record an event: routed when ``manager_id`` is given, else unrouted.

        :param event_id: Pre-generated id (bare 32-char hex).
        :param owner_user_id: The owner, or ``None`` in single-user mode.
        :param event_type: What happened, e.g. ``"session.turn.finished"``.
        :param title: One-line summary for the broker and the manager.
        :param payload: Details of the event.
        :param source: The system that reported it, e.g. ``"github"``.
        :param source_key: What the source reported on, e.g. a pull request URL.
        :param source_offset: Which report this is, e.g. a comment id.
        :param source_session_id: The session the event came from.
        :param tags: Routing hints.
        :param task_id: The task the event concerns.
        :param manager_id: The manager the event is routed to.
        :returns: The new event.
        """

    @abstractmethod
    def get(self, event_id: str, *, owner_user_id: str | None) -> TaskEvent | None:
        """Return the owner's event, or ``None`` when missing or someone else's."""

    @abstractmethod
    def get_by_source(
        self,
        *,
        owner_user_id: str | None,
        event_type: str,
        source: str | None,
        source_key: str | None,
        source_offset: str | None,
    ) -> TaskEvent | None:
        """Return the owner's first event for the same report from a source, or ``None``."""

    @abstractmethod
    def list(
        self,
        *,
        owner_user_id: str | None,
        states: Collection[str] | None = None,
        manager_id: str | None = None,
        fyi_cluster_ids: Collection[str] | None = None,
        queue_item_ids: Collection[str] | None = None,
        event_type: str | None = None,
        source_key: str | None = None,
        limit: int | None = None,
    ) -> builtins.list[TaskEvent]:
        """
        List the owner's events, oldest first.

        :param owner_user_id: The owner whose events to read.
        :param states: Only events in these states, or every state when ``None``.
        :param manager_id: Only events routed to this manager.
        :param fyi_cluster_ids: Only events filed into these FYI clusters.
        :param queue_item_ids: Only events these queue items carry.
        :param event_type: Only events of this type, e.g. ``"session.turn.finished"``.
        :param source_key: Only events about this thing, e.g. a session id.
        :param limit: The most events to return, or ``None`` for all.
        """

    @abstractmethod
    def list_waiting(self, *, state: str, max_deliveries: int) -> builtins.list[TaskEvent]:
        """
        List every owner's events in ``state`` that wait to be queued, oldest first.

        For the background packagers. An event waits when no queue item holds
        it, or its last item is done, and fewer than ``max_deliveries`` sent
        items carried it since it last passed to another role.
        """

    @abstractmethod
    def route_to_manager(
        self, event_ids: Collection[str], *, owner_user_id: str | None, manager_id: str
    ) -> builtins.list[TaskEvent] | None:
        """
        Route the owner's events to one manager, all or none.

        Events already routed to that manager stay as they are. A newly
        routed event leaves its queue item, so the manager's item can carry
        it, and its delivery count starts again.

        :returns: The events in request order, or ``None``, changing nothing,
            when one is missing, someone else's, or neither unrouted nor
            routed to this manager.
        """

    @abstractmethod
    def dismiss(self, event_id: str, *, owner_user_id: str | None) -> TaskEvent | None:
        """
        Mark the owner's event dismissed: handled, or noise.

        A dismissed event stays as it is.

        :returns: The event, or ``None`` when missing or someone else's.
        """
