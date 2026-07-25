# Task distributor manual (deferred)

The task-distributor agent is registered but not wired into the live routing path
yet. Stalled events go to the task secretary via the in-memory secretary queue.

When enabled in a future change, the distributor may handle multi-candidate
stalls with a cheaper model before escalating to the secretary.

See `docs/agent-tasks/TASK_DISTRIBUTOR.md` workflow notes below for the intended
API shape once activated.

## Intended workflow (future)

Batch-triage stalled task events. Route only when you are confident; otherwise
leave events for the task secretary.

### Trigger

`[System: distributor batch — route confident matches]`

### Workflow

For each event in the batch:

1. Read `event_id`, title, summary, and candidate task scores from ambiguous inbox.
2. When one active task is a clear match, call
   `POST /v1/task-events/{event_id}/resolve` with `{"task_id": "<id>"}`.
3. When no task is a confident fit, do **nothing** for that event.

Unresolved events escalate to the task secretary.

## Do not

- Create routing proposals or FYI clusters (secretary-only)
- Dispatch workers or accept board cards
