"""Task event type helpers for routing vs manager-internal lanes."""

from __future__ import annotations

SESSION_EVENT_PREFIX = "session."


def is_session_internal_event(event_type: str) -> bool:
    """Return whether an event belongs to the session adoption lane."""
    return event_type.startswith(SESSION_EVENT_PREFIX)


def is_distributor_candidate(*, event_type: str, task_id: str | None) -> bool:
    """Return whether an event should enter the distributor."""
    _ = task_id
    return not is_session_internal_event(event_type)
