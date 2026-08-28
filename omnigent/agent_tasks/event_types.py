"""Task event type helpers for routing vs manager-internal lanes."""

from __future__ import annotations

SESSION_EVENT_PREFIX = "session."

# An orphan-session event: a session that needs a routing profile and an adoption
# proposal. Born ``awaiting_grouping`` so the broker packager polls it like any
# other stalled event, instead of a direct wake.
SESSION_ORPHAN_EVENT_TYPE = "session.orphan"

# A session adoption proposal — created by the broker after triaging an orphan
# or discovered session. Born ``routed`` to the task so the user can accept or
# reject it on the task card.
SESSION_ADOPTION_PROPOSAL = "session.adoption"

# A session was adopted — emitted after the user accepts a proposal. Born
# ``routed`` to the task so the manager triages the new external worker.
SESSION_ADOPTED = "session.adopted"

# A worker execution settled. Emitted by the completion hook (born ``routed`` to
# the task) so the manager packager polls it like any other routed event instead
# of being woken directly.
WORKER_EXECUTION_FINISHED_EVENT_TYPE = "worker.execution.finished"

# A human action item was marked done by the user. Born ``routed`` to the task so
# the manager packager polls it like any other routed event.
HUMAN_ACTION_DONE_EVENT_TYPE = "item.human_action.done"

# An external session was discovered by a host-side watcher poll plugin.
# Born ``awaiting_grouping`` so the broker triages it (adopt or FYI).
EXTERNAL_SESSION_DISCOVERED_EVENT_TYPE = "external.session.discovered"

# An external session's transcript advanced. Auto-routed to the task bound to
# the session (via ``target_id`` on the Worker row).
EXTERNAL_SESSION_UPDATED_EVENT_TYPE = "external.session.updated"


def is_session_internal_event(event_type: str) -> bool:
    """Return whether an event belongs to the session adoption lane."""
    return event_type.startswith(SESSION_EVENT_PREFIX)


def is_ingress_candidate(event_type: str) -> bool:
    """Return whether an event should enter the ingress router."""
    return not is_session_internal_event(event_type)
