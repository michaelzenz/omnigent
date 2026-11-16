"""Persistence for the keys that each user's GlobalHub tasks follow.

A task follows the keys that its manager subscribes it to, from any source,
and the session events of its worker sessions (the workers table holds
those); only the manager decides. An event that a live task follows goes to
that task's manager; other events go to the broker. Keys match exactly: the
role manuals tell the agents to write each key in one canonical form, e.g. a
pull request URL without a query or a page after its number. Every read and
write is scoped to the owner.
"""

from __future__ import annotations

import builtins
from abc import ABC, abstractmethod
from dataclasses import dataclass

from omnigent.entities import TaskSubscription

#: The longest key that a subscription stores.
MAX_SOURCE_KEY_CHARS = 512


class SourceKeyError(ValueError):
    """A key that a task cannot follow: empty, or longer than ``MAX_SOURCE_KEY_CHARS``."""


@dataclass(frozen=True)
class Follower:
    """A live task that follows a key, and the manager that gets its events.

    :param task_id: The task.
    :param manager_id: The task's manager, or ``None`` while it has none.
    """

    task_id: str
    manager_id: str | None


class TaskSubscriptionStore(ABC):
    """Abstract base for the persistence of the keys that GlobalHub tasks follow."""

    def __init__(self, storage_location: str) -> None:
        """
        :param storage_location: Backend-specific storage URI, e.g.
            ``"sqlite:///chat.db"`` for SQLAlchemy.
        """
        self.storage_location = storage_location

    @abstractmethod
    def subscribe(
        self,
        subscription_id: str,
        *,
        owner_user_id: str | None,
        task_id: str,
        source_key: str,
        source: str | None = None,
    ) -> TaskSubscription:
        """
        Make the owner's task follow a key, or return the subscription it has.

        :param subscription_id: Id for a new subscription (bare 32-char hex).
        :param owner_user_id: The task's owner, or ``None`` in single-user mode.
        :param task_id: The task.
        :param source_key: The key, e.g. ``"https://github.com/org/repo/pull/12"``;
            events match it exactly.
        :param source: The system of the key, e.g. ``"github"``.
        :returns: The subscription.
        :raises SourceKeyError: When the key is empty or too long.
        """

    @abstractmethod
    def list_for_task(
        self, task_id: str, *, owner_user_id: str | None
    ) -> builtins.list[TaskSubscription]:
        """List the keys that the owner's task follows, in the order it started to follow them."""

    @abstractmethod
    def list_live(self, *, owner_user_id: str | None) -> builtins.list[TaskSubscription]:
        """List the keys that the owner's live tasks follow, oldest first."""

    @abstractmethod
    def delete(self, subscription_id: str, *, owner_user_id: str | None, task_id: str) -> bool:
        """Stop the owner's task from following a key; return whether the subscription existed."""

    @abstractmethod
    def followers(
        self, *, owner_user_id: str | None, source: str | None, source_key: str
    ) -> builtins.list[Follower]:
        """
        Return the owner's live tasks that follow an event, each once.

        A task follows the keys of its subscriptions, from any source. It also
        follows the session events of the sessions that work on it.

        :param owner_user_id: The owner of the event, or ``None`` in single-user mode.
        :param source: The event's source; ``SESSION_EVENT_SOURCE`` marks a session event.
        :param source_key: The event's key, matched exactly.
        :returns: The tasks with their managers; an empty list sends the event to the broker.
        """
