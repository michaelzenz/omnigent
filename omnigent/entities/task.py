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
    :param manager_agent_id: Manager agent bound to this task.
    :param owner_user_id: Owning user, or ``None`` in single-user mode.
    :param title: Human-readable task title.
    :param description: Canonical task description. ``None`` when unset.
    :param charter: Keyword-dense routing charter maintained by the manager.
    :param search_text: Plain searchable mirror of title, charter, and tags.
    :param state: One of ``"active"``, ``"paused"``, ``"done"``, ``"archived"``.
    :param manager_conversation_id: Manager session for this task, or ``None``
        before bootstrap.
    :param created_at: Unix epoch seconds at row creation.
    :param updated_at: Unix epoch seconds of the last write, or ``None``.
    """

    id: str
    manager_agent_id: str
    owner_user_id: str | None
    title: str
    description: str | None
    charter: str | None
    search_text: str
    state: str
    created_at: int
    manager_conversation_id: str | None = None
    updated_at: int | None = None


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
    :param task_id: Routed task, or ``None`` before routing completes.
    :param event_type: Machine-readable classifier, e.g. ``"build.finished"``.
    :param title: Human-readable one-liner for the event.
    :param payload: JSON payload string. ``None`` when unset.
    :param source: Event source, e.g. ``"github"`` or ``"ci"``.
    :param search_text: Plain searchable mirror used for routing.
    :param summary: Extraction done at ingestion for routing. ``None`` when unset.
    :param state: Routing/handling lifecycle state.
    :param priority: Routing queue priority; higher sorts first.
    :param selected_routing_attempt_id: Winning routing attempt, or ``None``.
    :param created_at: Unix epoch seconds at row creation.
    :param updated_at: Unix epoch seconds of the last write, or ``None``.
    :param routed_at: Unix epoch seconds when routing completed, or ``None``.
    :param processed_at: Unix epoch seconds when the manager finished handling,
        or ``None``.
    :param manager_agent_id: Denormalized routed manager agent, or ``None``
        before routing.
    :param manager_conversation_id: Denormalized manager session wake target,
        or ``None`` before routing.
    :param source_key: Stable external id for ingress dedupe, or ``None``.
    :param source_offset: Ingress cursor (e.g. byte offset), or ``None``.
    :param source_session_id: Omnigent session the event originated from,
        or ``None``.
    """

    id: str
    event_type: str
    title: str
    search_text: str
    state: str
    priority: int
    created_at: int
    task_id: str | None = None
    payload: str | None = None
    source: str | None = None
    summary: str | None = None
    selected_routing_attempt_id: str | None = None
    manager_agent_id: str | None = None
    manager_conversation_id: str | None = None
    source_key: str | None = None
    source_offset: int | None = None
    source_session_id: str | None = None
    updated_at: int | None = None
    routed_at: int | None = None
    processed_at: int | None = None


@dataclass
class TaskEventTag:
    """
    A typed tag on a task event.

    :param event_id: Event this tag belongs to.
    :param tag_type: Tag dimension.
    :param tag: Tag value within the dimension.
    """

    event_id: str
    tag_type: str
    tag: str


@dataclass
class TaskEventRoutingAttempt:
    """
    One routing proposal from a task event to a candidate task manager.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param event_id: Event being routed.
    :param candidate_task_id: Proposed destination task.
    :param candidate_manager_agent_id: Manager agent for the candidate task.
    :param rank: Shortlist position (1 = best retrieval score).
    :param score: Retrieval score, or ``None`` when unset.
    :param decision: One of ``"proposed"``, ``"accepted"``, ``"rejected"``,
        ``"selected"``, ``"not_selected"``.
    :param manager_reason: Manager justification. ``None`` when unset.
    :param proposed_at: Unix epoch seconds when the proposal was sent.
    :param responded_at: Unix epoch seconds when the manager responded,
        or ``None``.
    :param selected_at: Unix epoch seconds when the user selected this attempt,
        or ``None``.
    """

    id: str
    event_id: str
    candidate_task_id: str
    candidate_manager_agent_id: str
    rank: int
    decision: str
    proposed_at: int
    score: float | None = None
    manager_reason: str | None = None
    responded_at: int | None = None
    selected_at: int | None = None


@dataclass
class TaskEventRoutingResolution:
    """
    Records the user's final routing choice when multiple managers accepted.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param event_id: Event that was resolved.
    :param selected_attempt_id: Winning routing attempt.
    :param selected_task_id: Task the event was routed to.
    :param selected_manager_agent_id: Manager agent that won the selection.
    :param resolved_by_user_id: User who made the selection, or ``None``.
    :param resolution_note: Optional note from the resolver.
    :param created_at: Unix epoch seconds when the resolution was recorded.
    """

    id: str
    event_id: str
    selected_attempt_id: str
    selected_task_id: str
    selected_manager_agent_id: str
    created_at: int
    resolved_by_user_id: str | None = None
    resolution_note: str | None = None


@dataclass
class TaskItem:
    """
    A manager-managed backlog unit on one task.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param task_id: Parent managed task.
    :param title: Short label shown in the inbox/work UI.
    :param state: Lifecycle state (draft through done).
    :param instructions: Dispatch instructions for the worker.
    :param worker_agent_id: Proposed or assigned worker agent.
    :param model: Model override for dispatch.
    :param host_id: Host override for dispatch.
    :param workspace: Workspace override for dispatch.
    :param harness: Harness override for dispatch.
    :param priority: Sort priority within a task backlog.
    :param created_by: ``"manager"``, ``"secretary"``, or ``"user"``.
    :param created_at: Unix epoch seconds at row creation.
    :param updated_at: Unix epoch seconds of the last write, or ``None``.
    """

    id: str
    task_id: str
    title: str
    state: str
    created_at: int
    instructions: str | None = None
    worker_agent_id: str | None = None
    model: str | None = None
    host_id: str | None = None
    workspace: str | None = None
    harness: str | None = None
    priority: int = 0
    created_by: str = "manager"
    updated_at: int | None = None


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
    :param event_id: Optional triggering event, or ``None``.
    :param task_id: Task the item belongs to.
    :param manager_agent_id: Manager that dispatched the worker.
    :param worker_agent_id: Worker that executed the event.
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
    manager_agent_id: str
    worker_agent_id: str
    status: str
    attempt_no: int
    assigned_at: int
    created_at: int
    event_id: str | None = None
    conversation_id: str | None = None
    started_at: int | None = None
    finished_at: int | None = None
    result_summary: str | None = None
    error: str | None = None
    error_code: str | None = None
    updated_at: int | None = None


@dataclass
class TaskSessionBinding:
    """
    Maps an Omnigent session to the task manager that owns it.

    :param session_id: Bound conversation id.
    :param task_id: Task the session belongs to.
    :param manager_agent_id: Manager agent for the task.
    :param binding_kind: One of ``"ambient"``, ``"worker"``, ``"manager"``.
    :param created_at: Unix epoch seconds when the binding was created.
    :param manager_conversation_id: Manager session wake target, or ``None``.
    """

    session_id: str
    task_id: str
    manager_agent_id: str
    binding_kind: str
    created_at: int
    manager_conversation_id: str | None = None
