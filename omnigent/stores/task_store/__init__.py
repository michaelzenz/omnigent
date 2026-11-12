"""GlobalHub task persistence.

Every read and write is scoped to the task's owner: another user's task reads
as missing.
"""

from __future__ import annotations

import builtins
from abc import ABC, abstractmethod
from collections.abc import Collection
from typing import Any

from omnigent.entities import Task

#: Sentinel for "leave unchanged" where ``None`` is itself a value to store.
UNSET: Any = object()


class TaskStore(ABC):
    """Abstract base for GlobalHub task persistence."""

    def __init__(self, storage_location: str) -> None:
        """
        :param storage_location: Backend-specific storage URI, e.g.
            ``"sqlite:///chat.db"`` for SQLAlchemy.
        """
        self.storage_location = storage_location

    @abstractmethod
    def create(
        self,
        task_id: str,
        *,
        owner_user_id: str | None,
        title: str,
        goal: str,
        description: str | None = None,
        internal_note: str | None = None,
        manager_id: str | None = None,
        state: str = "active",
        priority: int = 2,
    ) -> Task:
        """
        Insert a task at the top of its owner's board.

        :param task_id: Pre-generated id (bare 32-char hex).
        :param owner_user_id: Owning user, or ``None`` in single-user mode.
        :param title: Short title shown on the board card.
        :param goal: The end state the task should reach.
        :param description: The user-facing overview.
        :param internal_note: Agent-facing context.
        :param manager_id: The owning manager, or ``None``.
        :param state: Initial state, e.g. ``"active"``.
        :param priority: Priority from 0 to 3.
        :returns: The new task.
        """

    @abstractmethod
    def get(self, task_id: str, *, owner_user_id: str | None) -> Task | None:
        """Return the owner's task, or ``None`` when missing or someone else's."""

    @abstractmethod
    def list(
        self,
        *,
        owner_user_id: str | None,
        states: Collection[str] | None = None,
        manager_id: str | None = None,
    ) -> builtins.list[Task]:
        """
        List the owner's tasks in board order (highest rank first).

        :param owner_user_id: The owner whose board to read.
        :param states: Only tasks in these states, or every state when ``None``.
        :param manager_id: Only this manager's tasks, when set.
        :returns: The matching tasks.
        """

    @abstractmethod
    def update(
        self,
        task_id: str,
        *,
        owner_user_id: str | None,
        title: str | None = None,
        description: str | None = None,
        internal_note: str | None = None,
        goal: str | None = None,
        manager_id: str | None = UNSET,
        state: str | None = None,
        priority: int | None = None,
    ) -> Task | None:
        """
        Change the given fields of the owner's task; ``None`` leaves a field as is.

        Entering ``agent-resolved`` records ``resolved_at``; leaving it clears
        that. A change stamps ``updated_at``.

        :param manager_id: The new manager, ``None`` to unassign, or
            :data:`UNSET` to leave it.
        :returns: The updated task, or ``None`` when missing or someone else's.
        """

    @abstractmethod
    def move_to_board_top(self, task_id: str, *, owner_user_id: str | None) -> Task | None:
        """Move the owner's task to the top of their board, or return ``None``."""

    @abstractmethod
    def move_to_board_end(self, task_id: str, *, owner_user_id: str | None) -> Task | None:
        """
        Move the owner's task toward the end of their board.

        An ``agent-resolved`` task goes to the very end. Any other task goes
        just above the first ``agent-resolved`` task, so live work stays
        ahead of resolved work.

        :returns: The moved task, or ``None`` when missing or someone else's.
        """

    @abstractmethod
    def delete(self, task_id: str, *, owner_user_id: str | None) -> bool:
        """Permanently delete the owner's task; return whether it existed."""
