"""GlobalHub task asset persistence.

Every read and write is scoped to the asset's owner, who is the task's owner:
another user's asset reads as missing.
"""

from __future__ import annotations

import builtins
from abc import ABC, abstractmethod
from collections.abc import Collection

from omnigent.entities import TaskAsset


class TaskAssetStore(ABC):
    """Abstract base for GlobalHub task asset persistence."""

    def __init__(self, storage_location: str) -> None:
        """
        :param storage_location: Backend-specific storage URI, e.g.
            ``"sqlite:///chat.db"`` for SQLAlchemy.
        """
        self.storage_location = storage_location

    @abstractmethod
    def upsert(
        self,
        asset_id: str,
        *,
        owner_user_id: str | None,
        task_id: str,
        url: str,
        title: str,
        category: str | None = None,
        add_related_session_ids: Collection[str] = (),
    ) -> TaskAsset:
        """
        Attach a URL to the owner's task, or update the asset the task has for it.

        An update keeps the asset's id, replaces its title, replaces its category
        unless ``category`` is ``None``, and adds ``add_related_session_ids`` to
        its related sessions. A new asset without a category is ``"other"``.

        :param asset_id: Pre-generated id (bare 32-char hex), used for a new asset.
        :param owner_user_id: The task's owner, or ``None`` in single-user mode.
        :param task_id: The task that carries the asset.
        :param url: Where the artifact lives.
        :param title: Short human-readable name of the artifact.
        :param category: Free-form label that groups assets on the task card,
            e.g. ``"documents"``.
        :param add_related_session_ids: Sessions that produced or used it.
        :returns: The new or updated asset.
        """

    @abstractmethod
    def list_for_tasks(
        self, task_ids: Collection[str], *, owner_user_id: str | None
    ) -> builtins.list[TaskAsset]:
        """List the owner's assets on these tasks, by task and then attach order."""

    @abstractmethod
    def delete(self, asset_id: str, *, owner_user_id: str | None, task_id: str) -> bool:
        """Detach the owner's asset from ``task_id``; return whether it was there."""
