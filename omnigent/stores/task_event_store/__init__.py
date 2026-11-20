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
        target_session_id: str | None = None,
    ) -> TaskEvent:
        """
        Record an event: routed when ``target_session_id`` is given, else unrouted.

        :param event_id: Pre-generated id (bare 32-char hex).
        :param owner_user_id: The owner, or ``None`` in single-user mode.
        :param event_type: What happened, e.g. ``"session.turn.finished"``.
        :param title: One-line summary for the broker and the session that gets it.
        :param payload: Details of the event.
        :param source: The source type, e.g. ``"github.pull_request"``.
        :param source_key: What the source reported on, e.g. a pull request URL.
        :param source_offset: Which report this is, e.g. a comment id.
        :param source_session_id: The session the event came from.
        :param tags: Routing hints.
        :param task_id: The task the event concerns.
        :param target_session_id: The session the event is routed to.
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
    def latest_routes(
        self, source: str, source_keys: Collection[str], *, owner_user_id: str | None
    ) -> dict[tuple[str, str], int]:
        """
        Return when the owner's events from each source key last went to each task.

        :param source: The system that reported the events, e.g. ``"session"``.
        :param source_keys: What the source reported on, e.g. session ids.
        :returns: The time of the latest event that went to a task, by
            ``(source_key, task_id)``.
        """

    @abstractmethod
    def list(
        self,
        *,
        owner_user_id: str | None,
        states: Collection[str] | None = None,
        target_session_id: str | None = None,
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
        :param target_session_id: Only events routed to this session.
        :param fyi_cluster_ids: Only events filed into these FYI clusters.
        :param queue_item_ids: Only events these queue items carry.
        :param event_type: Only events of this type, e.g. ``"session.turn.finished"``.
        :param source_key: Only events about this thing, e.g. a session id.
        :param limit: The most events to return, or ``None`` for all.
        """

    @abstractmethod
    def list_waiting_all_workspaces(
        self, *, state: str, max_deliveries: int
    ) -> builtins.list[TaskEvent]:
        """
        List the events in ``state`` in every workspace that wait to be queued, oldest first.

        For the background packagers, which run outside a request and act on
        each event inside its ``workspace_scope``. An event waits when no queue
        item holds it, or its last item is done, and fewer than
        ``max_deliveries`` sent items carried it since it last passed to
        another role.
        """

    @abstractmethod
    def route_to_session(
        self,
        event_ids: Collection[str],
        *,
        owner_user_id: str | None,
        session_id: str,
        task_id: str | None = None,
    ) -> builtins.list[TaskEvent] | None:
        """
        Route the owner's events to one session, e.g. a manager's, all or none.

        Events already routed to that session for the same task stay as they
        are. A newly routed event leaves its queue item, so the session's item
        can carry it, and its delivery count starts again.

        :param task_id: The task that the broker found for the events, which
            each newly routed event records, or ``None`` for no task.
        :returns: The events in request order, or ``None``, changing nothing,
            when one is missing, someone else's, or neither unrouted nor
            routed to this session for the same task.
        """

    @abstractmethod
    def dismiss(self, event_id: str, *, owner_user_id: str | None) -> TaskEvent | None:
        """
        Mark the owner's event dismissed: handled, or noise.

        A dismissed event stays as it is.

        :returns: The event, or ``None`` when missing or someone else's.
        """

    @abstractmethod
    def assign(
        self, event_id: str, *, owner_user_id: str | None, task_id: str
    ) -> TaskEvent | None:
        """
        Record the task that the owner's event belongs to, and mark the event handled.

        :param event_id: The event.
        :param owner_user_id: The event's owner, or ``None`` in single-user mode.
        :param task_id: The task, e.g. the one its manager filed the event into.
        :returns: The event, or ``None`` when missing or someone else's.
        """
