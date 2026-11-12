"""GlobalHub task entity, persisted in the ``tasks`` table.

A :class:`Task` is a long-lived unit of work on one user's GlobalHub board,
owned by one manager.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Task:
    """
    A task on a user's GlobalHub board.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param owner_user_id: Owning user, e.g. ``"alice@example.com"``, or
        ``None`` in single-user mode.
    :param title: Short title shown on the board card.
    :param goal: The end state the task should reach.
    :param state: ``"active"``, ``"idle"``, ``"agent-resolved"`` or
        ``"archived"``.
    :param created_at: Unix epoch seconds at row creation.
    :param manager_id: The manager that owns the task, or ``None`` before one
        is assigned.
    :param description: The user-facing overview the manager maintains.
    :param internal_note: Agent-facing context the manager maintains.
    :param priority: Priority from 0 to 3.
    :param queue_rank: Board order among the owner's tasks; higher ranks list
        first.
    :param updated_at: Unix epoch seconds of the last change, or ``None``.
    :param resolved_at: When the task entered ``agent-resolved``, or ``None``.
    """

    id: str
    owner_user_id: str | None
    title: str
    goal: str
    state: str
    created_at: int
    manager_id: str | None = None
    description: str | None = None
    internal_note: str | None = None
    priority: int = 2
    queue_rank: int = 0
    updated_at: int | None = None
    resolved_at: int | None = None
