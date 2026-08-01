"""Task event type helpers for routing vs manager-internal lanes."""

from __future__ import annotations

SESSION_EVENT_PREFIX = "session."

# An orphan-session event: a session that needs a routing profile and an adoption
# proposal. Born ``awaiting_grouping`` so the secretary packager polls it like any
# other stalled event, instead of a direct wake.
SESSION_ORPHAN_EVENT_TYPE = "session.orphan"


def is_session_internal_event(event_type: str) -> bool:
    """Return whether an event belongs to the session adoption lane."""
    return event_type.startswith(SESSION_EVENT_PREFIX)


def is_distributor_candidate(*, event_type: str, task_id: str | None) -> bool:
    """Return whether an event should enter the distributor."""
    _ = task_id
    return not is_session_internal_event(event_type)
