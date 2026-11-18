"""Persistence for the sources that GlobalHub sessions follow, and the watcher of each type.

A session subscribes to a source: a type such as ``"github.pull_request"`` and
a key such as the pull request URL. Each event about the key goes to every
session that follows it; an event that no session follows goes to the broker.
Keys match exactly, so the store keeps one spelling of each: a trimmed key,
and for a web address, no fragment and no final slash. The custodian assigns
each source type to the watcher whose manual covers it; the fallback watcher
checks the types with no assignment. Every read and write is scoped to the
owner.
"""

from __future__ import annotations

import builtins
import re
from abc import ABC, abstractmethod
from collections.abc import Collection
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from omnigent.entities import RESERVED_SOURCE_TYPE_PREFIX, SourceWatcher, Subscription

#: The longest source type that a subscription stores.
MAX_SOURCE_TYPE_CHARS = 64
#: The longest source key that a subscription stores.
MAX_SOURCE_KEY_CHARS = 512
#: The most words in the note of a subscription.
MAX_SUBSCRIPTION_NOTE_WORDS = 300
#: The most characters that a note stores, about ten for each word.
MAX_SUBSCRIPTION_NOTE_CHARS = 3000
_SOURCE_TYPE = re.compile(r"[a-z0-9][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*)+")


class SourceError(ValueError):
    """A source type or key that a session cannot follow."""


def normalize_source_type(source_type: str) -> str:
    """
    Return the one spelling of a source type: lowercase ``<system>.<kind>``.

    :param source_type: E.g. ``" GitHub.Pull_Request "``.
    :returns: E.g. ``"github.pull_request"``.
    :raises SourceError: When the type has no dot, other characters, or is too long.
    """
    cleaned = source_type.strip().lower()
    if len(cleaned) > MAX_SOURCE_TYPE_CHARS or not _SOURCE_TYPE.fullmatch(cleaned):
        raise SourceError(
            "a source type is <system>.<kind> in lowercase letters, digits, '_' and '-', "
            f'at most {MAX_SOURCE_TYPE_CHARS} characters, e.g. "github.pull_request"'
        )
    return cleaned


def normalize_source_key(source_key: str) -> str:
    """
    Return the one spelling of a source key: trimmed; a web address loses its fragment
    and its final slash.

    :param source_key: E.g. ``"https://github.com/org/repo/pull/12/#issuecomment-1"``.
    :returns: E.g. ``"https://github.com/org/repo/pull/12"``.
    :raises SourceError: When the key is empty or too long.
    """
    cleaned = source_key.strip()
    if cleaned.lower().startswith(("http://", "https://")):
        parts = urlsplit(cleaned)
        cleaned = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), parts.query, ""))
    if not cleaned or len(cleaned) > MAX_SOURCE_KEY_CHARS:
        raise SourceError(f"a source key has 1 to {MAX_SOURCE_KEY_CHARS} characters")
    return cleaned


def normalize_note(note: str) -> str:
    """
    Return a note without the spaces around it; a blank note is ``""``.

    :param note: Why a session follows a source and, for a source that is hard to
        read, how a watcher reads it.
    :returns: The note.
    :raises SourceError: When the note has more than :data:`MAX_SUBSCRIPTION_NOTE_WORDS`
        words or :data:`MAX_SUBSCRIPTION_NOTE_CHARS` characters.
    """
    cleaned = note.strip()
    if len(cleaned.split()) > MAX_SUBSCRIPTION_NOTE_WORDS:
        raise SourceError(f"a note has at most {MAX_SUBSCRIPTION_NOTE_WORDS} words")
    if len(cleaned) > MAX_SUBSCRIPTION_NOTE_CHARS:
        raise SourceError(f"a note has at most {MAX_SUBSCRIPTION_NOTE_CHARS} characters")
    return cleaned


def is_reserved_source_type(source_type: str) -> bool:
    """Whether only the server records events for the type, e.g. ``"omnigent.session"``."""
    return source_type.startswith(RESERVED_SOURCE_TYPE_PREFIX)


@dataclass(frozen=True)
class SubscribeResult:
    """
    A subscription, and whether its type is new to the owner.

    :param subscription: The session's subscription to the source.
    :param new_type: Whether no other subscription and no assignment had the type, so
        the custodian has a type to assign.
    """

    subscription: Subscription
    new_type: bool


@dataclass(frozen=True)
class SourceTypeUse:
    """
    One source type of the owner, with its watcher.

    :param source_type: E.g. ``"github.pull_request"``.
    :param watcher_id: The assigned watcher, or ``None`` for the fallback watcher.
    :param subscriptions: How many subscriptions have the type.
    """

    source_type: str
    watcher_id: str | None
    subscriptions: int


class SubscriptionStore(ABC):
    """Abstract base for the persistence of session subscriptions and source watchers."""

    def __init__(self, storage_location: str) -> None:
        """
        :param storage_location: Backend-specific storage URI, e.g.
            ``"sqlite:///chat.db"`` for SQLAlchemy.
        """
        self.storage_location = storage_location

    @abstractmethod
    def subscribe(
        self,
        *,
        owner_user_id: str | None,
        session_id: str,
        source_type: str,
        source_key: str,
        note: str | None = None,
    ) -> SubscribeResult:
        """
        Make a session follow a source, or update the type and note of the one it has.

        :param owner_user_id: The session's owner, or ``None`` in single-user mode.
        :param session_id: The session that follows the source.
        :param source_type: The source type; :func:`normalize_source_type` spells it.
        :param source_key: The source key; :func:`normalize_source_key` spells it.
        :param note: Why the session follows it and, for a source that is hard to
            read, how a watcher reads it; ``None`` keeps the saved note.
        :returns: The subscription, and whether its type is new to the owner.
        :raises SourceError: When the type, the key or the note is not valid.
        """

    @abstractmethod
    def unsubscribe(self, *, owner_user_id: str | None, session_id: str, source_key: str) -> bool:
        """Stop a session from following a source; return whether it followed it."""

    @abstractmethod
    def list_for_session(
        self, session_id: str, *, owner_user_id: str | None
    ) -> builtins.list[Subscription]:
        """List the sources that a session follows, in the order it started to follow them."""

    @abstractmethod
    def list_for_types(
        self, source_types: Collection[str], *, owner_user_id: str | None
    ) -> builtins.list[Subscription]:
        """List the owner's subscriptions of the given types, oldest first."""

    @abstractmethod
    def list_unassigned(self, *, owner_user_id: str | None) -> builtins.list[Subscription]:
        """List the owner's subscriptions whose type has no watcher, oldest first.

        The server's own types, e.g. ``"omnigent.session"``, are left out.
        """

    @abstractmethod
    def subscribers(self, *, owner_user_id: str | None, source_key: str) -> builtins.list[str]:
        """Return the sessions that follow a source key, in the order they started to follow it."""

    @abstractmethod
    def source_types(self, *, owner_user_id: str | None) -> builtins.list[SourceTypeUse]:
        """List the owner's source types, from subscriptions and assignments, by name.

        The server's own types are left out.
        """

    @abstractmethod
    def copy_session(
        self, source_session_id: str, target_session_id: str, *, owner_user_id: str | None
    ) -> int:
        """Make a session follow the sources that another session follows; return how many.

        Each copy keeps the note and the time the other session started to
        follow the source. The server's own types and the sources that the
        session already follows are left out.
        """

    @abstractmethod
    def delete_for_session(self, session_id: str, *, owner_user_id: str | None) -> int:
        """Delete every subscription of a session; return how many."""

    @abstractmethod
    def delete_for_keys(
        self,
        source_keys: Collection[str],
        *,
        owner_user_id: str | None,
        created_before: int | None = None,
    ) -> int:
        """Delete the owner's subscriptions to the given source keys; return how many.

        :param created_before: Only the subscriptions created at or before this
            Unix epoch second, when set.
        """

    @abstractmethod
    def assign_type(
        self, *, owner_user_id: str | None, source_type: str, watcher_id: str
    ) -> SourceWatcher:
        """Assign a source type to a watcher, in place of any watcher it had.

        :raises SourceError: When the type is not valid, or is one of the server's own.
        """

    @abstractmethod
    def unassign_type(self, *, owner_user_id: str | None, source_type: str) -> bool:
        """Leave a source type to the fallback watcher; return whether it had a watcher."""

    @abstractmethod
    def source_watchers(self, *, owner_user_id: str | None) -> builtins.list[SourceWatcher]:
        """List the owner's assignments, by source type."""

    @abstractmethod
    def types_for_watcher(
        self, watcher_id: str, *, owner_user_id: str | None
    ) -> builtins.list[str]:
        """List the source types assigned to a watcher, by name."""
