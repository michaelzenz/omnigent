"""Notice formatting for the agent queue packagers.

The manager and secretary are no longer woken directly — routed events (and the
``worker.execution.finished`` event the completion hook emits) are polled by the
role packagers. This module holds only the notice text the packagers render at
send time.
"""

from __future__ import annotations

import json
import logging

from omnigent.agent_tasks.event_types import SESSION_ORPHAN_EVENT_TYPE

_logger = logging.getLogger(__name__)

WORKER_EXECUTION_FINISHED_EVENT_TYPE = "worker.execution.finished"


def _format_manager_notice(events: list) -> str:
    """Format the notice the manager packager hands the dispatcher.

    One notice per task per dispatch, listing every routed event the manager has
    not yet reconciled. A ``worker.execution.finished`` event carries its
    outcome in the JSON ``payload``; any other routed event is shown by type and
    title, matching the old per-event wake text.
    """
    lines = [
        f"[System: {len(events)} event(s) routed to this task — triage or act]",
    ]
    for event in events:
        if event.event_type == WORKER_EXECUTION_FINISHED_EVENT_TYPE:
            detail = _format_execution_detail(event)
            lines.append(f"- {event.event_type}: {detail}")
        else:
            lines.append(f"- {event.event_type}: {event.title!r} (routed)")
    return "\n".join(lines)


def _format_execution_detail(event) -> str:
    """Render a worker.execution.finished event's payload as a one-liner."""
    item_title = event.title
    status = "finished"
    summary = ""
    if event.payload:
        try:
            payload = json.loads(event.payload)
        except (json.JSONDecodeError, TypeError):
            payload = None
        if isinstance(payload, dict):
            item_title = payload.get("item_title") or item_title
            status = payload.get("status") or status
            summary = (payload.get("result_summary") or payload.get("error") or "").strip()
    summary_block = f" — {summary}" if summary else ""
    return f"Worker execution {status} for item {item_title!r}{summary_block}"


def _format_secretary_stall_notice(events: list) -> str:
    """Format the notice the secretary packager hands the dispatcher.

    A batch for one user can mix two kinds of stalled work: routed business events
    that need triage (``awaiting_grouping``) and orphan sessions that need a
    routing profile (``session.orphan``). Each gets its own instruction line so
    the secretary knows which action to take per event.
    """
    orphans = [e for e in events if e.event_type == SESSION_ORPHAN_EVENT_TYPE]
    routed = [e for e in events if e.event_type != SESSION_ORPHAN_EVENT_TYPE]
    lines = ["[System: task event(s) need routing — resolve or escalate]"]
    if routed:
        lines.append("List ambiguous inbox, route confident matches via resolve, else board card.")
        for event in routed:
            lines.append(f"- {event.event_type}: {event.title!r} ({event.state})")
    if orphans:
        lines.append(
            "Read each orphan session, write omnigent.task.routing_repo (and "
            "optional omnigent.task.routing_intent), then call propose-adoption. "
            "User must accept before adopt."
        )
        for event in orphans:
            session_id = event.source_key or "?"
            lines.append(f"- session.orphan: {event.title!r} ({session_id})")
    return "\n".join(lines)
