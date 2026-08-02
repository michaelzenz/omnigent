"""Notice formatting for the agent queue packagers.

The manager and secretary are no longer woken directly — routed events (and the
``worker.execution.finished`` event the completion hook emits) are polled by the
role packagers. This module holds only the notice text the packagers render at
send time.
"""

from __future__ import annotations

import json
import logging

from omnigent.agent_tasks.event_types import (
    WORKER_EXECUTION_FINISHED_EVENT_TYPE,
)

_logger = logging.getLogger(__name__)


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


def _format_secretary_stall_notice(
    events: list,
    *,
    clusters: list | None = None,
    candidate_task_ids: list[str] | None = None,
    is_orphan: bool = False,
) -> str:
    """Format the notice the secretary packager hands the dispatcher.

    Returns a JSON string the secretary reads directly. A routed batch carries
    ``clusters`` (each with its tags and full event entries, similar events kept
    contiguous) plus ranked ``candidate_task_ids``, so it can reconcile/route
    without a follow-up ``ambiguous-inbox``/``match-tasks`` call. An orphan batch
    carries the adoption steps in the prompt and a flat ``events`` list (no
    candidates).
    """
    from omnigent.agent_tasks.secretary_inbox import event_notice_entry

    if is_orphan:
        prompt = (
            "[System: please triage and route these events]\n"
            "Read each orphan session, write omnigent.task.routing_repo (and "
            "optional omnigent.task.routing_intent), then call propose-adoption. "
            "User must accept before adopt."
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
