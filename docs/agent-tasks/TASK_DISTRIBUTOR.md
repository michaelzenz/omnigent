# Task distributor manual

Batch-triage stalled task events. Route only when you are confident; otherwise
leave events for the task secretary.

## Trigger

`[System: distributor batch — route confident matches]` — one or more events
are in `awaiting_grouping` with keyword candidate scores for reference.

## Workflow

For each event in the batch:

1. Read `event_id`, title, summary, and candidate task scores.
2. When one active task is a clear match, call
   `POST /v1/task-events/{event_id}/resolve` with
   `{"resolution": "route_to_task", "task_id": "<id>"}`.
3. When no task is a confident fit, do **nothing** for that event.

Unresolved events are escalated to the task secretary after your turn.

## Do not

- Create routing proposals or FYI clusters (secretary-only)
- Dispatch workers or accept board cards
- Route when multiple tasks tie or the match is weak
