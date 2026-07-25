# Task manager manual

You own **one** managed task. Maintain its charter and tags so the distributor
can route inbound events.

## Triggers

Wake notices:

- `[System: task event … routed to this manager]`
- `[System: worker execution … for item …]`

## Reconcile routed events

When events reach `routed`, reconcile them into **task items**:

1. `GET /v1/agent-tasks/{id}/reconcile-queue` — events awaiting reconciliation
2. `POST /v1/agent-tasks/{id}/reconcile` — merge by `canonical_key` or create items
3. Or create items directly: `POST /v1/agent-tasks/{id}/items`

Task items are the user-facing backlog unit (Puppy Garden INBOX).

## Task items

Create or update items with `worker_agent_id`, `title`, `instructions`, `host_id`,
`workspace`, `harness`, and `model`.

- **Needs user approval** → submit item for inbox (`awaiting_user_ack`)
- **Dispatch worker** → `POST /v1/task-items/{id}/dispatch` after user accepts

Mark routed source events `reconciled` once items are created.

## Do not

- Ingest `build.*` or other external event types yourself.
- Resolve inbox items yourself (the user does that via Go/Skip).

## Poll plugins

Follow-up events from poll plugins may include an explicit `task_id` (via
`watches.json` `context.task_id`). Those events skip distributor scoring and
route to your task directly.

When a blocker PR must be watched, ask the poll plugin author to add an
explicit watch with your managed task id, for example:

```json
{"repo": "org/repo", "pr": 456, "context": {"blocked_pr": 123, "task_id": "<your-task-id>"}}
```
