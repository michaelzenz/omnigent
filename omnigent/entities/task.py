"""Agent-task entities — persisted task/event routing and execution tables.

A :class:`Task` is a long-lived unit of work owned by one manager agent.
:class:`TaskEvent` rows are inbound signals. :class:`TaskItem` rows are
manager-managed backlog units. Workers run against items via
:class:`TaskEventExecution`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Task:
    """
    A managed task persisted in the ``tasks`` table.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param manager_role_key: Glossary manager template key, e.g. ``"manager:default"``.
        The role resolves the agent profile that runs the task manager.
    :param owner_user_id: Owning user, or ``None`` in single-user mode.
    :param title: Human-readable task title.
    :param description: Canonical task description. ``None`` when unset.
    :param internal_note: Agent-facing routing context maintained by the manager.
    :param goal: The endstate this task should land on. Required for every task.
    :param state: One of ``"active"``, ``"pending"``, ``"idle"``, ``"archived"``.
    :param manager_conversation_id: Manager session for this task, or ``None``
        before bootstrap.
    :param created_at: Unix epoch seconds at row creation.
    :param updated_at: Unix epoch seconds of the last write, or ``None``.
    """

    id: str
    manager_role_key: str
    owner_user_id: str | None
    title: str
    description: str | None
    internal_note: str | None
    state: str
    created_at: int
    goal: str
    manager_conversation_id: str | None = None
    updated_at: int | None = None
    priority: int = 2
    queue_rank: int = 0


@dataclass
class TaskTag:
    """
    A typed tag on a task.

    :param task_id: Task this tag belongs to.
    :param tag_type: Tag dimension, e.g. ``"domain"`` or ``"component"``.
    :param tag: Tag value within the dimension.
    """

    task_id: str
    tag_type: str
    tag: str


@dataclass
class TaskEvent:
    """
    An inbound event that may be routed to a task manager.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param event_type: Machine-readable classifier, e.g. ``"build.finished"``.
    :param title: Human-readable one-liner for the event.
    :param state: Routing/handling lifecycle state.
    :param created_at: Unix epoch seconds at row creation.
    :param tags: Immutable ingress tags used for routing. ``None`` when unset.
    :param task_id: Routed task, or ``None`` before routing completes.
    :param payload: JSON payload string. ``None`` when unset.
    :param source: Event source, e.g. ``"github"`` or ``"ci"``. ``None`` when unset.
    :param source_key: Stable dedupe key within ``source`` (external ingress id or
        adopted session id for secretary/adoption events). ``None`` when unset.
    :param source_offset: Per-source_key dedup cursor string, or ``None``.
    :param source_internal_session_id: Originating PuppyGarden conversation when
        the event was emitted from an internal session. ``None`` when unset.
    :param parent_event_id: Canonical ingress event this row was fanned out from
        for a subscription delivery. ``None`` on canonical (ingress) rows.
    :param updated_at: Unix epoch seconds of the last write, or ``None``.
    :param routed_at: Unix epoch seconds when routing completed, or ``None``.
    :param processed_at: Unix epoch seconds when the manager finished handling,
        or ``None``.
    """

    id: str
    event_type: str
    title: str
    state: str
    created_at: int
    owner_user_id: str | None = None
    tags: list[EventTag] | None = None
    task_id: str | None = None
    payload: str | None = None
    source: str | None = None
    source_key: str | None = None
    source_offset: str | None = None
    source_internal_session_id: str | None = None
    parent_event_id: str | None = None
    updated_at: int | None = None
    routed_at: int | None = None
    processed_at: int | None = None


@dataclass
class EventTag:
    """
    A typed ingress tag on a task event (stored inline on ``task_events.tags``).

    :param tag_type: Tag dimension.
    :param tag: Tag value within the dimension.
    """

    tag_type: str
    tag: str


@dataclass
class TaskEventRoutingAttempt:
    """
    Record of how an event was routed to a task (for monitoring).

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param event_id: Event being routed.
    :param candidate_task_id: Destination task.
    :param proposed_at: Unix epoch seconds when routing was recorded.
    :param score: Tag-overlap score, or ``None`` when unset.
    :param reason: Human-readable routing explanation from the ingress scorer or
        broker. ``None`` when unset.
    """

    id: str
    event_id: str
    candidate_task_id: str
    proposed_at: int
    score: float | None = None
    reason: str | None = None


@dataclass
class TaskEventSubscription:
    """
    A task's subscription to an event ``(source, source_key)`` pair.

    When an ingress event matches a live subscription, the server fans out a
    per-task event copy routed to that task.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param task_id: Subscriber task.
    :param source: Event source to match, e.g. ``"poll_plugin:github_pr"``.
    :param source_key: Stable key within ``source`` to match, e.g. ``"org/repo#1"``.
    :param created_at: Unix epoch seconds at row creation.
    :param owner_user_id: User who created the subscription, or ``None``.
    """

    id: str
    task_id: str
    source: str
    source_key: str
    created_at: int
    owner_user_id: str | None = None


@dataclass
class Worker:
    """
    A worker slot on a managed task.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param task_id: Parent managed task.
    :param kind: ``"managed"`` for dispatched workers, ``"external"`` for adopted sessions.
    :param role_key: Legacy role key, retained for compatibility with old rows.
    :param target_id: Durable session/thread id assigned by the target system.
    :param created_at: Unix epoch seconds at row creation.
    :param updated_at: Unix epoch seconds of the last write, or ``None``.
    """

    id: str
    task_id: str
    kind: str
    created_at: int
    target_id: str | None = None
    state: str = "uninitialized"
    needs_response: bool = False
    provider_name: str | None = None
    provider_configuration: str | None = None
    failure_reason: str | None = None
    last_observed_at: int | None = None
    updated_at: int | None = None


@dataclass
class TaskItem:
    """
    A manager-managed backlog unit on one task.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param task_id: Parent managed task.
    :param title: Short label shown in the inbox/work UI.
    :param state: Lifecycle state (draft through done).
    :param description: User-facing reason this item exists.
    :param instructions: Dispatch instructions for the worker.
    :param internal_note: Agent-facing context to avoid re-querying sources.
    :param worker_id: Assigned worker slot, or ``None`` while still in the inbox.
    :param created_by: ``"manager"``, ``"broker"``, or ``"user"``.
    :param created_at: Unix epoch seconds at row creation.
    :param updated_at: Unix epoch seconds of the last write, or ``None``.
    """

    id: str
    task_id: str
    title: str
    state: str
    created_at: int
    description: str | None = None
    instructions: str | None = None
    internal_note: str | None = None
    worker_id: str | None = None
    created_by: str = "manager"
    updated_at: int | None = None


@dataclass
class TaskAsset:
    """A link or file reference attached to one managed task."""

    id: int
    task_id: str
    kind: str
    title: str
    created_at: int
    url: str | None = None
    category: str = "other"


@dataclass
class TaskItemEvent:
    """Link between a task item and a contributing task event."""

    task_item_id: str
    event_id: str
    relation: str
    created_at: int


@dataclass
class FyiCluster:
    """Secretary cluster of informational orphan events."""

    id: str
    owner_user_id: str
    headline: str
    rationale: str | None
    state: str
    created_at: int
    resolved_at: int | None = None


@dataclass
class TaskEventExecution:
    """
    One worker execution attempt for a task item.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param task_item_id: Backlog item being executed.
    :param task_id: Task the item belongs to.
    :param agent_queue_item_id: Queue delivery that created this attempt.
    :param status: One of ``"queued"``, ``"running"``, ``"succeeded"``,
        ``"failed"``, ``"cancelled"``.
    :param attempt_no: Monotonic attempt number for this event.
    :param assigned_at: Unix epoch seconds when the worker was assigned.
    :param conversation_id: Worker session, or ``None`` when unset.
    :param started_at: Unix epoch seconds when execution started, or ``None``.
    :param finished_at: Unix epoch seconds when execution finished, or ``None``.
    :param result_summary: Outcome summary. ``None`` when unset.
    :param error: Failure detail. ``None`` unless failed.
    :param error_code: Short failure classification. ``None`` unless failed.
    :param created_at: Unix epoch seconds at row creation.
    :param updated_at: Unix epoch seconds of the last write, or ``None``.
    """

    id: str
    task_item_id: str
    task_id: str
    agent_queue_item_id: str | None
    status: str
    attempt_no: int
    assigned_at: int
    created_at: int
    conversation_id: str | None = None
    started_at: int | None = None
    finished_at: int | None = None
    result_summary: str | None = None
    error: str | None = None
    error_code: str | None = None
    updated_at: int | None = None
