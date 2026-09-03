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
    EXTERNAL_SESSION_UPDATED_EVENT_TYPE,
    SESSION_TURN_FINISHED_EVENT_TYPE,
    WORKER_EXECUTION_FINISHED_EVENT_TYPE,
)

_logger = logging.getLogger(__name__)


def _is_session_event(event_type: str) -> bool:
    """Whether this event is a session-watcher event eligible for batch summary."""
    return event_type.startswith("session.") or event_type == EXTERNAL_SESSION_UPDATED_EVENT_TYPE


def _format_manager_notice(
    events: list,
    task_titles: dict | None = None,
    task_states: dict | None = None,
) -> str:
    """Format the notice the manager packager hands the dispatcher.

    One notice per manager session per dispatch — possibly spanning several
    tasks when tasks share a manager — listing every routed event the manager
    has not yet reconciled. Events without a task are explicitly manager-routed
    so the manager can select or create their task. Session events for the same
    session are summarized as a single entry.
    """
    titles = task_titles or {}
    task_ids: list[str] = []
    for event in events:
        task_id = getattr(event, "task_id", None)
        if task_id and task_id not in task_ids:
            task_ids.append(task_id)

    def _task_scope(task_id: str | None) -> str:
        title = titles.get(task_id or "")
        return f"{title!r} ({task_id})" if title else (task_id or "?")

    unassigned_count = sum(1 for event in events if getattr(event, "task_id", None) is None)
    if len(task_ids) == 1 and not unassigned_count:
        scope = f"task {_task_scope(task_ids[0])}"
    elif task_ids:
        scope = f"{len(task_ids)} tasks: " + ", ".join(_task_scope(t) for t in task_ids)
    else:
        scope = "this manager"
    lines = [f"[System: {len(events)} event(s) routed to {scope} — triage or act]"]
    if unassigned_count:
        lines.append(
            f"[{unassigned_count} manager-routed event(s) have no task; "
            "select an existing task or create one before reconciling them.]"
        )
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
            lines.append(f"- {_label(event)}{event.event_type}: {detail}")
        else:
            lines.append(f"- {_label(event)}{event.event_type}: {event.title!r} (routed)")
    for session_evts in session_groups.values():
        if len(session_evts) == 1:
            event = session_evts[0]
            if event.event_type == EXTERNAL_SESSION_UPDATED_EVENT_TYPE:
                lines.append(_format_external_update_notice(event))
            elif event.event_type == SESSION_TURN_FINISHED_EVENT_TYPE:
                lines.append(_format_turn_finished_notice(event))
            else:
                lines.append(f"- {_label(event)}{event.event_type}: {event.title!r} (routed)")
        else:
            lines.append(_format_session_batch_notice(session_evts))
    if task_states:
        # Roster footer: the manager's whole portfolio, so it never has to
        # re-query which tasks it owns.
        roster = ", ".join(f"{tid} ({state})" for tid, state in sorted(task_states.items()))
        lines.append(f"[Your tasks: {roster}]")
    return "\n".join(lines)


def _label(event) -> str:
    """Routing label prefix for one event line."""
    task_id = getattr(event, "task_id", None)
    return f"[task:{task_id}] " if task_id else "[manager-routed; task unassigned] "


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
) -> str:
    """Format the notice the broker packager hands the dispatcher.

    Returns clustered events and directs the broker to select or create a
    first-class manager. Task selection and reconciliation belong to managers.
    """
    from omnigent.agent_tasks.broker_inbox import event_notice_entry

    prompt = (
        "[System: route these events to managers] "
        "List the active managers and compare their descriptions with each "
        "cluster. Route each cluster to the best host-compatible manager. "
        "If none fits, create a manager with an accurate scope description, "
        "then route the cluster to it. Do not select or create tasks."
    )
    payload: dict[str, object] = {
        "prompt": prompt,
        "clusters": [
            {
                "tags": c.tags,
                "events": [event_notice_entry(e) for e in c.events],
            }
            for c in (clusters or [])
        ],
    }
    return json.dumps(payload, separators=(",", ":"))
