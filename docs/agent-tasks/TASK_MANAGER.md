# Task manager manual

You own **one** managed task. Maintain its charter and tags so the distributor
can route inbound events.

## Triggers

Wake notices:

- `[System: task event … routed to this manager]`
- `[System: worker execution … for event …]`

## Inbound triage (`awaiting_manager_triage`)

When an external event is routed to you:

1. Read the event title, summary, and payload.
2. Decide:
   - **No action** → `POST /v1/task-events/{id}/complete`
   - **Needs user approval** → `POST /v1/agent-tasks/{id}/events` with
     `event_type: manager.proposal`
   - **Dispatch worker** → create `manager.work_item` or `POST …/dispatch`

You must call `complete` before the inbound event is considered handled.

## Proposals

Proposal payload must include `worker_agent_id`, `title`, `instructions`,
`host_id`, `workspace`, `harness`, and `model`.

## Do not

- Ingest `build.*` or other external event types yourself.
- Resolve your own proposals (the user does that).

## Poll plugins

Follow-up events from poll plugins may include an explicit `task_id` (via
`watches.json` `context.task_id`). Those events skip distributor scoring and
land on your triage queue directly.

When a blocker PR must be watched, ask the poll plugin author to add an
explicit watch with your managed task id, for example:

```json
{"repo": "org/repo", "pr": 456, "context": {"blocked_pr": 123, "task_id": "<your-task-id>"}}
```
