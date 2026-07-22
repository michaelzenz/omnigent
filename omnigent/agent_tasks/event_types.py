"""Task event type helpers for routing vs manager-internal lanes."""

from __future__ import annotations

MANAGER_PROPOSAL = "manager.proposal"
MANAGER_WORK_ITEM = "manager.work_item"
MANAGER_EVENT_PREFIX = "manager."


def is_manager_internal_event(event_type: str) -> bool:
    """Return whether an event was created by a task manager (not ingress)."""
    return event_type.startswith(MANAGER_EVENT_PREFIX)


def is_distributor_candidate(*, event_type: str, task_id: str | None) -> bool:
    """Return whether an event should enter the distributor candidate pool."""
    return task_id is None and not is_manager_internal_event(event_type)
