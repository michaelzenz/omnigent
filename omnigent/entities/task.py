"""GlobalHub board entities, persisted in the ``tasks``, ``managers``, ``workers`` and
``task_events`` tables.

A :class:`Task` is a long-lived unit of work on one user's GlobalHub board,
owned by one :class:`Manager`. Each :class:`Worker` attaches a session that
works on the task. A :class:`TaskEvent` is a signal the broker routes to a
manager.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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


@dataclass
class Manager:
    """
    A manager on a user's GlobalHub board: the durable identity behind its session.

    Tasks and events refer to the manager, never to its session, so the session
    can be recreated without losing the manager's portfolio.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param owner_user_id: Owning user, or ``None`` in single-user mode.
    :param title: Title of the manager's session.
    :param description: The scope the manager owns, written by the manager
        itself; empty until it writes one.
    :param created_at: Unix epoch seconds at row creation.
    :param updated_at: Unix epoch seconds of the last change.
    :param conversation_id: The manager's current session, or ``None`` while it
        has none.
    :param host_id: Host the manager's session runs on, or ``None`` for the
        owner's GlobalHub host.
    :param workspace: Workspace on that host, or ``None`` for the owner's
        GlobalHub workspace.
    """

    id: str
    owner_user_id: str | None
    title: str
    description: str
    created_at: int
    updated_at: int
    conversation_id: str | None = None
    host_id: str | None = None
    workspace: str | None = None


@dataclass
class Worker:
    """
    A worker: one session wrapped for one GlobalHub task.

    The session's own status (busy, waiting for the user, gone) is read from
    the session, never copied onto the worker.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param task_id: The task this worker serves. A session working on several
        tasks has one worker per task.
    :param owner_user_id: Owning user (the task's owner), or ``None`` in
        single-user mode.
    :param session_id: The wrapped session.
    :param created_at: When the session was attached to the task.
    :param title: Short label for the session's recent work on this task,
        maintained by the manager.
    :param updated_at: Unix epoch seconds of the last change, or ``None``.
    """

    id: str
    task_id: str
    owner_user_id: str | None
    session_id: str
    created_at: int
    title: str | None = None
    updated_at: int | None = None


@dataclass(frozen=True)
class EventTag:
    """
    A routing hint on a task event.

    :param tag_type: What the hint names, e.g. ``"repo"``.
    :param tag: The hint, e.g. ``"example-org/storage"``.
    """

    tag_type: str
    tag: str


@dataclass
class TaskEvent:
    """
    A signal on a user's GlobalHub board, e.g. a session finishing a turn.

    The broker routes an ``unrouted`` event to one of the owner's managers. The
    manager handles a ``routed`` event and dismisses it, or files it into an
    FYI cluster.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param owner_user_id: Owning user, or ``None`` in single-user mode.
    :param event_type: What happened, e.g. ``"session.turn.finished"``.
    :param title: One-line summary for the broker and the manager.
    :param state: ``"unrouted"``, ``"routed"``, ``"dismissed"`` or ``"fyi"``.
    :param created_at: Unix epoch seconds at row creation.
    :param payload: Details of the event.
    :param source: The system that reported it, e.g. ``"github"``.
    :param source_key: What the source reported on, e.g. a pull request URL.
    :param source_offset: Which report this is, e.g. a comment id. With the
        source, key and event type it identifies a repeated report.
    :param source_session_id: The session the event came from.
    :param tags: Routing hints.
    :param task_id: The task the event concerns.
    :param manager_id: The manager the event was routed to.
    :param updated_at: Unix epoch seconds of the last change.
    :param routed_at: When the event was routed to a manager.
    :param processed_at: When the event was dismissed or filed.
    """

    id: str
    owner_user_id: str | None
    event_type: str
    title: str
    state: str
    created_at: int
    payload: dict[str, Any] = field(default_factory=dict)
    source: str | None = None
    source_key: str | None = None
    source_offset: str | None = None
    source_session_id: str | None = None
    tags: list[EventTag] = field(default_factory=list)
    task_id: str | None = None
    manager_id: str | None = None
    updated_at: int | None = None
    routed_at: int | None = None
    processed_at: int | None = None
