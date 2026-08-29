"""Notice formatting for the agent queue packagers.

The manager and broker are no longer woken directly — routed events (and the
``worker.execution.finished`` event the completion hook emits) are polled by the
role packagers. This module holds only the notice text the packagers render at
send time.
"""

from __future__ import annotations

import json
import logging

from omnigent.agent_tasks.event_types import (
    EXTERNAL_SESSION_DISCOVERED_EVENT_TYPE,
    EXTERNAL_SESSION_UPDATED_EVENT_TYPE,
    SESSION_TURN_FINISHED_EVENT_TYPE,
    WORKER_EXECUTION_FINISHED_EVENT_TYPE,
)

_logger = logging.getLogger(__name__)


def _is_session_event(event_type: str) -> bool:
    """Whether this event is a session-watcher event eligible for batch summary."""
    return event_type.startswith("session.") or event_type == EXTERNAL_SESSION_UPDATED_EVENT_TYPE


def _format_manager_notice(events: list) -> str:
    """Format the notice the manager packager hands the dispatcher.

    One notice per task per dispatch, listing every routed event the manager has
    not yet reconciled. Session events for the same session are summarized as a
    single entry so the agent sees "3 turns finished" rather than 3 copies.
    """
    lines = [
        f"[System: {len(events)} event(s) routed to this task — triage or act]",
    ]
    # Group session events by source_key for summarization.
    session_groups: dict[str, list] = {}
    other_events: list = []
    for event in events:
        if _is_session_event(event.event_type) and event.source_key:
            session_groups.setdefault(event.source_key, []).append(event)
        else:
            other_events.append(event)
    for event in other_events:
        if event.event_type == WORKER_EXECUTION_FINISHED_EVENT_TYPE:
            detail = _format_execution_detail(event)
            lines.append(f"- {event.event_type}: {detail}")
        else:
            lines.append(f"- {event.event_type}: {event.title!r} (routed)")
    for session_evts in session_groups.values():
        if len(session_evts) == 1:
            event = session_evts[0]
            if event.event_type == EXTERNAL_SESSION_UPDATED_EVENT_TYPE:
                lines.append(_format_external_update_notice(event))
            elif event.event_type == SESSION_TURN_FINISHED_EVENT_TYPE:
                lines.append(_format_turn_finished_notice(event))
            else:
                lines.append(f"- {event.event_type}: {event.title!r} (routed)")
        else:
            lines.append(_format_session_batch_notice(session_evts))
    return "\n".join(lines)


def _format_session_batch_notice(events: list) -> str:
    """Summarize multiple events for the same session as a single entry."""
    event_type = events[0].event_type
    count = len(events)
    payload: dict = {}
    if events[0].payload:
        try:
            payload = json.loads(events[0].payload)
        except (json.JSONDecodeError, TypeError):
            payload = {}
    session_title = payload.get("session_title") or payload.get("session_hint", "?")
    session_id = payload.get("session_id") or payload.get("session_hint", "?")
    if event_type == SESSION_TURN_FINISHED_EVENT_TYPE:
        return (
            f"- {event_type}: Session '{session_title}' finished {count} turns since last check\n"
            f"  Session ID: {session_id}\n"
            f"  Read the session transcript to see what was done. "
            f"Reconcile into task items if relevant."
        )
    if event_type == EXTERNAL_SESSION_UPDATED_EVENT_TYPE:
        deltas = []
        for event in events:
            p: dict = {}
            if event.payload:
                try:
                    p = json.loads(event.payload)
                except (json.JSONDecodeError, TypeError):
                    pass
            delta = p.get("transcript_delta", "")
            if delta:
                deltas.append(delta)
        parts = [
            f"- {event_type}: External session '{session_title}' updated {count} times since last check"
        ]
        if deltas:
            parts.append(f"  Combined transcript delta ({len(deltas)} updates):\n" + "\n---\n".join(deltas))
        parts.append(
            "  Review the delta. Update item states if the work is done. "
            "If follow-up is needed, suggest a new taskItem (Copy button)."
        )
        return "\n".join(parts)
    return f"- {event_type}: {count} events for session '{session_title}' (routed)"


def _format_external_update_notice(event) -> str:
    """Render an external.session.updated event as a structured manager prompt."""
    payload: dict = {}
    if event.payload:
        try:
            payload = json.loads(event.payload)
        except (json.JSONDecodeError, TypeError):
            payload = {}
    session_hint = payload.get("session_hint", "?")
    rewind_at = payload.get("rewind_at")
    delta = payload.get("transcript_delta", "")

    if rewind_at is not None:
        header = (
            f"- {event.event_type}: External session '{session_hint}' rewound "
            f"(divergence after {rewind_at})"
        )
    else:
        header = f"- {event.event_type}: External session '{session_hint}' updated"

    parts = [header]
    if delta:
        parts.append(f"  Transcript delta:\n{delta}")
    parts.append(
        "  Review the delta. Update item states if the work is done. "
        "If follow-up is needed, suggest a new taskItem (Copy button)."
    )
    return "\n".join(parts)


def _format_turn_finished_notice(event) -> str:
    """Render a session.turn.finished event as a manager prompt."""
    payload: dict = {}
    if event.payload:
        try:
            payload = json.loads(event.payload)
        except (json.JSONDecodeError, TypeError):
            payload = {}
    session_title = payload.get("session_title", "?")
    session_id = payload.get("session_id", "?")
    return (
        f"- {event.event_type}: Session '{session_title}' finished a turn\n"
        f"  Session ID: {session_id}\n"
        f"  Read the session transcript to see what was done. "
        f"Reconcile into task items if relevant."
    )


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


def _format_worker_notice(event) -> str:
    """Format a structured notice for a single worker.execution.finished event.

    Unlike ``_format_manager_notice`` (which batches multiple events into a
    one-liner-per-event summary), this produces a rich, structured prompt with
    the worker's full output so the manager can decide whether to update the
    task or suggest a new taskItem.
    """
    payload: dict = {}
    if event.payload:
        try:
            payload = json.loads(event.payload)
        except (json.JSONDecodeError, TypeError):
            payload = {}
    status = payload.get("status", "finished")
    item_title = payload.get("item_title", event.title)
    instructions = payload.get("instructions")
    summary = (payload.get("result_summary") or payload.get("error") or "").strip()
    output = payload.get("output")

    lines = [
        f"[System: Worker finished a turn (status: {status})]",
        f"Item: {item_title}",
    ]
    if instructions:
        lines.append(f"Instructions sent to worker: {instructions}")
    if summary:
        lines.append(f"Worker summary: {summary}")
    if output:
        lines.append(f"Worker output:\n{output}")
    lines.append(
        "Review the worker's output. Update the task status. "
        "If follow-up work is needed, suggest a new taskItem for user review."
    )
    return "\n".join(lines)


def _format_broker_stall_notice(
    events: list,
    *,
    clusters: list | None = None,
    candidate_task_ids: list[str] | None = None,
    is_orphan: bool = False,
) -> str:
    """Format the notice the broker packager hands the dispatcher.

    Returns a JSON string the broker reads directly. A routed batch carries
    ``clusters`` (each with its tags and full event entries, similar events kept
    contiguous) plus ranked ``candidate_task_ids``, so it can reconcile/route
    without a follow-up ``ambiguous-inbox``/``match-tasks`` call. An orphan batch
    carries the adoption steps in the prompt and a flat ``events`` list (no
    candidates).
    """
    from omnigent.agent_tasks.broker_inbox import event_notice_entry

    if is_orphan:
        is_discovered = events and events[0].event_type == EXTERNAL_SESSION_DISCOVERED_EVENT_TYPE
        if is_discovered:
            prompt = (
                "[System: an external session was discovered by the watcher] "
                "Read the transcript_snippet in the event payload to understand what "
                "the session is working on. Decide one of three outcomes:\n"
                "1. Adopt to an existing task — call "
                "POST /v1/agent-tasks/sessions/{session_id}/adopt "
                "with {\"task_id\": \"<id>\"}.\n"
                "2. Adopt to a new task — create a new pending task via "
                "POST /v1/agent-tasks/packages, then call adopt.\n"
                "3. FYI cluster — call POST /v1/task-events/fyi-clusters.\n"
                "User must accept the adoption before it takes effect."
            )
        else:
            prompt = (
                "[System: orphan session needs triage] "
                "The event payload includes the session title, the user's "
                "last message, and the agent's last response. Read them to "
                "understand what the session is working on, then follow the "
                "orphan session adoption section in your manual."
            )
        payload: dict[str, object] = {
            "prompt": prompt,
            "events": [event_notice_entry(e) for e in events],
        }
    else:
        prompt = (
            "[System: please triage and route these events] "
            "The following are possible clusters waiting for route/reconcile."
        )
        payload = {
            "prompt": prompt,
            "clusters": [
                {
                    "tags": c.tags,
                    "events": [event_notice_entry(e) for e in c.events],
                }
                for c in (clusters or [])
            ],
            "candidate_task_ids": candidate_task_ids or [],
        }
    return json.dumps(payload, separators=(",", ":"))
