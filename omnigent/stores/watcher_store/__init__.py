"""GlobalHub watcher persistence.

Every read and write is scoped to the watcher's owner: another user's watcher
reads as missing.
"""

from __future__ import annotations

import builtins
from abc import ABC, abstractmethod
from collections.abc import Collection
from typing import Any

from omnigent.entities import Watcher, WatcherProblem

#: Sentinel for "leave unchanged" where ``None`` is itself a value to store.
UNSET: Any = object()


class WatcherStore(ABC):
    """Abstract base for GlobalHub watcher persistence."""

    def __init__(self, storage_location: str) -> None:
        """
        :param storage_location: Backend-specific storage URI, e.g.
            ``"sqlite:///chat.db"`` for SQLAlchemy.
        """
        self.storage_location = storage_location

    @abstractmethod
    def create(
        self,
        watcher_id: str,
        *,
        owner_user_id: str | None,
        name: str,
        description: str = "",
        manual: str | None = None,
        model: str | None = None,
        builtin: bool = False,
    ) -> Watcher:
        """
        Insert a watcher. Its session comes with its first run.

        :param watcher_id: Pre-generated id (bare 32-char hex).
        :param owner_user_id: Owning user, or ``None`` in single-user mode.
        :param name: The watcher's name, also the title of its session.
        :param description: What the watcher watches.
        :param manual: The user's manual, or ``None`` for the packaged template.
        :param model: Model for the watcher's session, or ``None`` for the
            watcher agent's own model.
        :param builtin: Whether it is the built-in fallback watcher.
        :returns: The new watcher.
        """

    @abstractmethod
    def get(self, watcher_id: str, *, owner_user_id: str | None) -> Watcher | None:
        """Return the owner's watcher, or ``None`` when missing or someone else's."""

    @abstractmethod
    def get_builtin(self, *, owner_user_id: str | None) -> Watcher | None:
        """Return the owner's built-in fallback watcher, or ``None`` before it exists."""

    @abstractmethod
    def get_by_conversation_id(
        self, conversation_id: str, *, owner_user_id: str | None
    ) -> Watcher | None:
        """Return the owner's watcher whose session is ``conversation_id``, or ``None``."""

    @abstractmethod
    def list(self, *, owner_user_id: str | None) -> builtins.list[Watcher]:
        """List the owner's watchers in creation order."""

    @abstractmethod
    def update(
        self,
        watcher_id: str,
        *,
        owner_user_id: str | None,
        name: str = UNSET,
        description: str = UNSET,
        manual: str | None = UNSET,
        model: str | None = UNSET,
        conversation_id: str | None = UNSET,
    ) -> Watcher | None:
        """
        Change the given fields of the owner's watcher.

        :data:`UNSET` leaves a field as it is; ``None`` is a value for the
        fields that take it. A change stamps ``updated_at``.

        :returns: The updated watcher, or ``None`` when missing or someone else's.
        """

    @abstractmethod
    def set_problem(
        self, watcher_id: str, problem: WatcherProblem | None, *, owner_user_id: str | None
    ) -> Watcher | None: ...

    @abstractmethod
    def set_cursor(
        self, watcher_id: str, cursor: str | None, *, owner_user_id: str | None
    ) -> Watcher | None: ...

    @abstractmethod
    def clear_problems(
        self, watcher_ids: Collection[str], *, owner_user_id: str | None
    ) -> int: ...

    @abstractmethod
    def settle_run_problem(
        self, conversation_id: str, *, started_at: int, error: str | None, now: int
    ) -> Watcher | None: ...

    @abstractmethod
    def delete(self, watcher_id: str, *, owner_user_id: str | None) -> Watcher | None:
        """
        Delete the owner's watcher, its schedule, its tracked items and its source types.

        One transaction deletes them all; the source types go to the fallback watcher.

        :returns: The deleted watcher, or ``None`` when missing or someone else's.
        """
