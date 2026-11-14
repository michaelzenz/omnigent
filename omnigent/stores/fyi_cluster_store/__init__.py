"""GlobalHub FYI cluster persistence.

Every read and write is scoped to the cluster's owner: another user's cluster
reads as missing. Filing events into a cluster and dismissing it also change
the events' states.
"""

from __future__ import annotations

import builtins
from abc import ABC, abstractmethod
from collections.abc import Collection

from omnigent.entities import FyiCluster


class FyiClusterStore(ABC):
    """Abstract base for GlobalHub FYI cluster persistence."""

    def __init__(self, storage_location: str) -> None:
        """
        :param storage_location: Backend-specific storage URI, e.g.
            ``"sqlite:///chat.db"`` for SQLAlchemy.
        """
        self.storage_location = storage_location

    @abstractmethod
    def create(
        self,
        cluster_id: str,
        *,
        owner_user_id: str | None,
        headline: str,
        event_ids: Collection[str],
        rationale: str | None = None,
    ) -> FyiCluster | None:
        """
        Create a pending cluster and file the owner's events into it, all or none.

        Only unrouted and routed events can be filed; filing marks them ``fyi``.

        :param cluster_id: Pre-generated id (bare 32-char hex).
        :param owner_user_id: The owner, or ``None`` in single-user mode.
        :param headline: One line that sums up the events.
        :param event_ids: The events to file.
        :param rationale: Why the events need no task.
        :returns: The new cluster, or ``None``, creating nothing, when an event
            is missing, someone else's, or neither unrouted nor routed.
        """

    @abstractmethod
    def extend(
        self,
        cluster_id: str,
        *,
        owner_user_id: str | None,
        headline: str,
        event_ids: Collection[str],
        rationale: str | None = None,
    ) -> FyiCluster | None:
        """
        File more of the owner's events into their pending cluster, all or none.

        ``headline`` replaces the cluster's headline. ``rationale`` replaces its
        rationale unless it is ``None``. Events already in the cluster stay as
        they are.

        :returns: The cluster, or ``None``, changing nothing, when it is not the
            owner's pending cluster or an event cannot be filed.
        """

    @abstractmethod
    def get(self, cluster_id: str, *, owner_user_id: str | None) -> FyiCluster | None:
        """Return the owner's cluster, or ``None`` when missing or someone else's."""

    @abstractmethod
    def list(
        self, *, owner_user_id: str | None, states: Collection[str] | None = None
    ) -> builtins.list[FyiCluster]:
        """List the owner's clusters oldest first, in every state when ``states`` is ``None``."""

    @abstractmethod
    def dismiss(self, cluster_id: str, *, owner_user_id: str | None) -> FyiCluster | None:
        """
        Dismiss the owner's cluster and the events filed into it.

        A dismissed cluster stays as it is.

        :returns: The cluster, or ``None`` when missing or someone else's.
        """
