"""Persistence for what each GlobalHub watcher tracks.

Every read and write is scoped to the watcher's owner. An observation saves
what the watcher sees now and, in the same transaction, records a task event
for each change it finds. A watcher can track any number of items; lists come
in pages.
"""

from __future__ import annotations

import builtins
from abc import ABC, abstractmethod
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from omnigent.entities import PagedList, Watcher, WatchItem

#: Event types of a change in a tracked item, and of a new item the watcher announces.
WATCH_CHANGED_EVENT_TYPE = "watch.changed"
WATCH_NEW_EVENT_TYPE = "watch.new"


class WatchItemLimitError(ValueError):
    """An observation would keep more offsets on an item than ``MAX_WATCH_ITEM_OFFSETS``."""


@dataclass(frozen=True)
class WatchObservation:
    """
    What a watcher sees now for one item.

    :param source: The system, e.g. ``"github"``.
    :param source_key: What is tracked, e.g. a pull request URL.
    :param offsets: The values to compare with the last run, by name; ``None``
        removes a name. Names the observation leaves out keep their value.
    :param title: A short label; ``None`` keeps the saved one.
    :param task_id: The task the item belongs to; ``None`` keeps the saved one.
    :param summary: A one-line explanation of a change, used as its event's title.
    :param details: More about a change for its event, e.g. failing check names.
    :param announce: Whether a new item records a ``watch.new`` event.
    """

    source: str
    source_key: str
    offsets: Mapping[str, str | None]
    title: str | None = None
    task_id: str | None = None
    summary: str | None = None
    details: Mapping[str, Any] | None = None
    announce: bool = False


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

    :param source: The item's system.
    :param source_key: What the item tracks.
    :param outcome: ``"new"``, ``"changed"`` or ``"unchanged"``.
    :param changes: The offsets that changed, in the observation's order.
    :param event_id: The task event recorded for the change or the new item, if any.
    """

    source: str
    source_key: str
    outcome: Literal["new", "changed", "unchanged"]
    changes: tuple[WatchChange, ...] = ()
    event_id: str | None = None


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
        task_id: str | None = None,
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
        :param task_id: Only this task's items, when given.
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
        item is a silent baseline unless its observation announces it; an item
        the watcher marked done becomes active again. An event goes to the
        manager of the item's task when that task is live and has one, else to
        the broker.

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
        watcher_id: str,
        keys: Sequence[tuple[str, str]],
        *,
        owner_user_id: str | None,
        now: int,
    ) -> builtins.list[tuple[str, str]]:
        """
        Mark a watcher's items done, so its runs stop checking them.

        :param watcher_id: The watcher.
        :param keys: The items, as ``(source, source_key)`` pairs.
        :param owner_user_id: The watcher's owner, or ``None`` in single-user mode.
        :param now: Current Unix epoch seconds.
        :returns: The keys of the items it does not track; the others are done.
        """

    @abstractmethod
    def purge_done(self, watcher_id: str, *, owner_user_id: str | None, before: int) -> int:
        """Delete the watcher's items marked done before ``before``; return how many."""
