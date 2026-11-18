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
        unmanaged: bool = False,
        limit: int | None = None,
        after: tuple[int, str] | None = None,
        before: tuple[int, str] | None = None,
    ) -> builtins.list[Task]:
        """
        List the owner's tasks in board order (highest rank first).

        :param owner_user_id: The owner whose board to read.
        :param states: Only tasks in these states, or every state when ``None``.
        :param manager_id: Only this manager's tasks, when set.
        :param unmanaged: Only tasks without a manager, when true.
        :param limit: At most this many tasks, or all when ``None``.
        :param after: Only tasks that come after this board position, a
            ``(queue_rank, id)`` pair, so that a board reads a page at a time.
        :param before: Only tasks that come before this board position; with
            ``limit``, the ones nearest to it, so that a board reads the page
            above another.
        :returns: The matching tasks.
        """

    @abstractmethod
    def count(
        self,
        *,
        owner_user_id: str | None,
        states: Collection[str] | None = None,
        unmanaged: bool = False,
    ) -> int:
        """
        Count the owner's tasks.

        :param owner_user_id: The owner whose board to read.
        :param states: Only tasks in these states, or every state when ``None``.
        :param unmanaged: Only tasks without a manager, when true.
        :returns: How many tasks match.
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
        manager_id: str | None = UNSET,
        state: str | None = None,
        priority: int | None = None,
        has_update: bool | None = None,
    ) -> Task | None:
        """
        Change the given fields of the owner's task; ``None`` leaves a field as is.

        Entering ``agent-resolved`` records ``resolved_at``; leaving it clears
        that. A change stamps ``updated_at``; a change of ``has_update`` does
        not count as one.

        :param manager_id: The new manager, ``None`` to unassign, or
            :data:`UNSET` to leave it.
        :param has_update: ``True`` flags news on the task for the user;
            ``False`` clears the flag.
        :returns: The updated task, or ``None`` when missing or someone else's.
        """

    @abstractmethod
    def record_work(self, task_id: str, *, owner_user_id: str | None) -> Task | None:
        """
        Record work on the owner's task, e.g. a worker's finished turn.

        An active or idle task becomes active, moves to the top of the board
        and counts as changed now. A resolved or archived task stays as it is.

        :returns: The task, or ``None`` when missing or someone else's.
        """

    @abstractmethod
    def idle_quiet_tasks_all_workspaces(self, *, changed_before: int) -> int:
        """
        Turn idle each active task, in every workspace, unchanged since ``changed_before``.

        The task keeps its place on the board and its ``updated_at``, so
        turning idle does not count as a change. The next work makes it active.

        :param changed_before: Unix epoch seconds; a task without a change
            counts from its creation.
        :returns: How many tasks became idle.
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
        """Permanently delete the owner's task, its workers and its assets.

        :returns: Whether the task existed.
        """
