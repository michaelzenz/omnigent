"""Resolve stalled task events onto a managed task manager."""

from __future__ import annotations

from typing import Any

from omnigent.agent_tasks.routing import route_event_to_task
from omnigent.entities import Task, TaskEvent
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.task_event_store import TaskEventStore
from omnigent.stores.task_store import TaskStore

ROUTABLE_STALLED_EVENT_STATES = frozenset(
    {
        "received",
        "awaiting_grouping",
        "pending_triage",
    }
)
_DISMISSABLE_STATES = frozenset(
    {
        "received",
        "awaiting_grouping",
        "pending_triage",
        "classified_fyi",
        "routed",
    }
)
# States where a dismiss is a no-op: the event already landed somewhere
# (reconciled into a task item, acked, fanned out) or settled terminally.
# Managers are told to ALWAYS dismiss leftover events after triage, so an
# already-settled event must be left untouched rather than rejected.
_DISMISS_NOOP_STATES = frozenset(
    {
        "reconciled",
        "dismissed",
        "failed",
        "broadcast",
    }
)


async def dismiss_task_event(
    *,
    event: TaskEvent,
    task_event_store: TaskEventStore,
) -> TaskEvent:
    """Mark an event dismissed without routing it to a manager.

    Idempotent: the event's CURRENT state is re-read from the store before
    dismissing — an event already in a settled state (reconciled, acked,
    dismissed, failed, broadcast) is returned unchanged, even when the
    caller holds a stale snapshot. Dismiss only applies to events still
    awaiting triage.
    """
    current = task_event_store.get_event(event.id)
    if current is None:
        raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)
    if current.state in _DISMISS_NOOP_STATES or current.state not in _DISMISSABLE_STATES:
        return current
    updated = task_event_store.update_event(current.id, state="dismissed")
    if updated is None:
        raise OmnigentError("Task event not found", code=ErrorCode.NOT_FOUND)
    return updated


async def resolve_task_event(
    *,
    event: TaskEvent,
    task_store: TaskStore,
    task_event_store: TaskEventStore,
    conversation_store: ConversationStore,
    task: Task,
    session_creator: Any | None = None,
    app_state: Any | None = None,
    user_id: str | None = None,
) -> TaskEvent:
    """Route a stalled event to a task manager, bootstrapping when needed.

    The event lands in ``routed`` state; the manager packager picks it up on its
    next poll, so this no longer wakes the manager directly.
    """
    if event.state not in ROUTABLE_STALLED_EVENT_STATES:
        raise OmnigentError(
            f"Cannot route event in state {event.state!r}",
            code=ErrorCode.CONFLICT,
        )

    return await route_event_to_task(
        event=event,
        task=task,
        task_store=task_store,
        task_event_store=task_event_store,
        conversation_store=conversation_store,
        routing_reason="broker-resolve",
        session_creator=session_creator,
        app_state=app_state,
        user_id=user_id,
    )
