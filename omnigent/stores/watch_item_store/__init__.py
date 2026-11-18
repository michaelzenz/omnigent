"""Persistence for what each GlobalHub watcher tracks.

Every read and write is scoped to the watcher's owner. An observation saves
what the watcher sees now and, in the same transaction, records the task events
for each change it finds: one for each session that follows the item's key, or
one for the broker. Marking an item done tells the sessions that follow it; a
done item whose offsets change again is reopened. A watcher can track any
number of items; lists come in pages.
"""

from __future__ import annotations

import builtins
from abc import ABC, abstractmethod
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from omnigent.entities import PagedList, Watcher, WatchItem

#: Event types of a change in a tracked item, of a new item the watcher announces, of an
#: item the watcher marks done, and of a done item that changes again.
WATCH_CHANGED_EVENT_TYPE = "watch.changed"
WATCH_NEW_EVENT_TYPE = "watch.new"
WATCH_DONE_EVENT_TYPE = "watch.done"
WATCH_REOPENED_EVENT_TYPE = "watch.reopened"


class WatchItemLimitError(ValueError):
    """An observation would keep more offsets on an item than ``MAX_WATCH_ITEM_OFFSETS``."""


@dataclass(frozen=True)
class WatchObservation:
    """
    What a watcher sees now for one item.

    :param source: The source type, e.g. ``"github.pull_request"``.
    :param source_key: What is tracked, e.g. a pull request URL.
    :param offsets: The values to compare with the last run, by name; ``None``
        removes a name. Names the observation leaves out keep their value.
    :param title: A short label; ``None`` keeps the saved one.
    :param summary: A one-line explanation of a change, used as its events' title.
    :param details: More about a change for its events, e.g. failing check names.
    :param announce: Whether a new item records ``watch.new`` events.
    """

    source: str
    source_key: str
    offsets: Mapping[str, str | None]
    title: str | None = None
    summary: str | None = None
    details: Mapping[str, Any] | None = None
    announce: bool = False


@dataclass(frozen=True)
class WatchFinish:
    """
    One item that a watcher marks done.

    :param source: The item's source type.
    :param source_key: What the item tracks.
    :param summary: A one-line reason, e.g. ``"Merged"``, used as its events' title.
    """

    source: str
    source_key: str
    summary: str | None = None


@dataclass(frozen=True)
class WatchChange:
    """One offset that changed: its name, and its values before and after."""

    offset: str
    before: str | None
    after: str | None


@dataclass(frozen=True)
class WatchObservationResult:
    """
    What an observation found for one item.

    :param source: The item's source type.
    :param source_key: What the item tracks.
    :param outcome: ``"new"``, ``"changed"``, ``"reopened"`` (a done item that
        changed) or ``"unchanged"``.
    :param changes: The offsets that changed, in the observation's order.
    :param event_ids: The task events recorded for the change or the new item:
        one for each session that follows the item, else one for the broker.
    """

    source: str
    source_key: str
    outcome: Literal["new", "changed", "reopened", "unchanged"]
    changes: tuple[WatchChange, ...] = ()
    event_ids: tuple[str, ...] = ()


class WatchItemStore(ABC):
    """Abstract base for the persistence of what each GlobalHub watcher tracks."""

    def __init__(self, storage_location: str) -> None:
        """
        :param storage_location: Backend-specific storage URI, e.g.
            ``"sqlite:///chat.db"`` for SQLAlchemy.
        """
        self.storage_location = storage_location

    @abstractmethod
    def list(
        self,
        watcher_id: str,
        *,
        owner_user_id: str | None,
        statuses: Collection[str] = ("active",),
        source: str | None = None,
        limit: int = 100,
        after: str | None = None,
    ) -> PagedList[WatchItem]:
        """
        List a page of a watcher's items, in the order it started to track them.

        The order does not change when the watcher observes an item, so the
        pages stay stable while it works through them.

        :param watcher_id: The watcher.
        :param owner_user_id: The watcher's owner, or ``None`` in single-user mode.
        :param statuses: The statuses to include, e.g. ``("active",)``.
        :param source: Only this system's items, when given.
        :param limit: At most this many items in the page.
        :param after: The ``last_id`` of the previous page.
        :raises omnigent.errors.StaleCursorError: When ``after`` names no item
            of the watcher, e.g. one purged since the previous page.
        """

    @abstractmethod
    def stalest(
        self, watcher_id: str, *, owner_user_id: str | None, limit: int
    ) -> builtins.list[WatchItem]:
        """Return up to ``limit`` active items of the watcher, least recently checked first."""

    @abstractmethod
    def count(
        self,
        watcher_id: str,
        *,
        owner_user_id: str | None,
        statuses: Collection[str] = ("active",),
    ) -> int:
        """Count a watcher's items in the given statuses."""

    @abstractmethod
    def observe(
        self,
        watcher: Watcher,
        observations: Sequence[WatchObservation],
        *,
        session_id: str | None,
        now: int,
    ) -> builtins.list[WatchObservationResult]:
        """
        Save what the watcher sees now, and record an event for each change.

        All observations are saved in one transaction with their events. A new
        item is a silent baseline unless its observation announces it. An item
        the watcher marked done stays done while its offsets stay the same; when
        they change, it becomes active again with a ``watch.reopened`` event.
        Each session that follows the item's key gets an event; when no session
        follows it, one event goes to the broker.

        :param watcher: The watcher that observes.
        :param observations: What it sees now, at most one per source and key.
        :param session_id: The watcher session the events come from.
        :param now: Current Unix epoch seconds.
        :returns: One result per observation, in order.
        :raises WatchItemLimitError: When an item would keep more than
            ``MAX_WATCH_ITEM_OFFSETS`` offsets (see :mod:`omnigent.entities`);
            nothing is saved.
        """

    @abstractmethod
    def finish(
        self,
        watcher: Watcher,
        items: Sequence[WatchFinish],
        *,
        session_id: str | None,
        now: int,
    ) -> builtins.list[tuple[str, str]]:
        """
        Mark a watcher's items done, and tell the sessions that follow them.

        Each session that follows a newly done item gets a ``watch.done`` event;
        the broker gets none. An item that is done already stays as it is.

        :param watcher: The watcher.
        :param items: The items, each with an optional reason.
        :param session_id: The watcher session the events come from.
        :param now: Current Unix epoch seconds.
        :returns: The ``(source, source_key)`` of the items it does not track;
            the others are done.
        """

    @abstractmethod
    def find(
        self,
        watcher_id: str,
        keys: Collection[tuple[str, str]],
        *,
        owner_user_id: str | None,
    ) -> builtins.list[WatchItem]:
        """Return the watcher's items with these ``(source, source_key)`` pairs, in any status."""

    @abstractmethod
    def list_done_before(
        self, watcher_id: str, *, owner_user_id: str | None, before: int
    ) -> builtins.list[WatchItem]:
        """List the watcher's items marked done before ``before``, oldest first."""

    @abstractmethod
    def purge_done(self, watcher_id: str, *, owner_user_id: str | None, before: int) -> int:
        """Delete the watcher's items marked done before ``before``; return how many."""

    @abstractmethod
    def delete_source(self, watcher_id: str, *, owner_user_id: str | None, source: str) -> int:
        """Delete the watcher's items of one source type, in any status; return how many."""
