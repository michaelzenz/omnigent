# Agent tasks API reference

All paths are under `/v1`. Auth: logged-in user unless noted.

## Ingress

`POST /v1/task-events` — ingest an external event. Auth: logged-in user or host
poller (`X-Omnigent-Host-Id`).

Producers describe the event only (`source`, `source_key`, `event_type`, `tags`,
`payload`); they never name a task. The server routes it: task event
subscriptions are matched on `source` + `source_key` first, then session
bindings, then tag-overlap scoring, then the broker.

Dedup key: `source` + `source_key` + `source_offset` + `event_type` (applies to
the canonical event; subscription fan-out copies do not re-dedup).

When one or more subscriptions match, the canonical event settles in the
`broadcast` state and each subscriber task gets its own routed copy; the
response then carries `deliveries: [{event_id, task_id}]`.

## Task events

| Method | Path |
|--------|------|
| GET | `/v1/task-events` |
| GET | `/v1/task-events/{id}` |
| POST | `/v1/task-events` |
| POST | `/v1/task-events/{id}/complete` |
| POST | `/v1/task-events/{id}/dismiss` |
| POST | `/v1/task-events/batch-resolve` |
| GET | `/v1/task-events/ambiguous-inbox` |
| POST | `/v1/task-events/match-tasks` |
| POST | `/v1/task-events/fyi-clusters` |

## Tasks

| Method | Path |
|--------|------|
| POST | `/v1/agent-tasks` |
| POST | `/v1/agent-tasks/packages` |
| GET | `/v1/agent-tasks` |
| POST | `/v1/agent-tasks/batch` |
| GET | `/v1/agent-tasks/{id}` |
| PATCH | `/v1/agent-tasks/{id}` |
| POST | `/v1/agent-tasks/{id}/move-to-queue-end` |
| POST | `/v1/agent-tasks/{id}/manager-queue-hold` |
| DELETE | `/v1/agent-tasks/{id}/manager-queue-hold/{token}` |
| DELETE | `/v1/agent-tasks/{id}` |
| PUT | `/v1/agent-tasks/{id}/tags` |
| GET | `/v1/agent-tasks/{id}/executions` |
| POST | `/v1/agent-tasks/{id}/event-subscriptions` |
| GET | `/v1/agent-tasks/{id}/event-subscriptions` |
| DELETE | `/v1/agent-tasks/{id}/event-subscriptions/{subscription_id}` |
| POST | `/v1/agent-tasks/{id}/bootstrap` |
| GET | `/v1/agent-tasks/{id}/dashboard` |
| GET | `/v1/agent-tasks/{id}/items` |
| POST | `/v1/agent-tasks/{id}/items` |
| POST | `/v1/agent-tasks/{id}/assets` |
| GET | `/v1/agent-tasks/{id}/reconcile-queue` |
| POST | `/v1/agent-tasks/{id}/ack` |
| POST | `/v1/agent-tasks/{id}/reconcile-events` |
| POST | `/v1/agent-tasks/{id}/accept-package` |
| POST | `/v1/agent-tasks/{id}/reject-package` |

Task create and update bodies accept `priority` as an integer from 0 (P0,
highest) through 3 (P3), defaulting to 2. Task responses include `priority` and
`queue_rank`. Lists are ordered by `queue_rank DESC, id DESC`. Creating a task
places it at the front; creating a task item bumps its parent to the front; the
move endpoint places it at the end without changing audit timestamps.

The dashboard task object includes `goal`, `created_at`, `priority`, and
`queue_rank`. It also returns every nonterminal, non-cancelled task item in
`active_items`, plus `recent_done_items.all` and
`recent_done_items.by_worker`, each capped at the three newest done items by
`updated_at DESC, id DESC`. Cancelled items are omitted from these V2 buckets
and from the user-facing task-item list.

Selecting a Manager acquires a tokenized, expiring queue hold. The hold blocks
new manager dispatches without changing a pre-existing active, paused, or halted
queue state; releasing it re-arms scanning. Opening a queued item's instruction
editor or worker picker similarly acquires an item edit lease. PATCH/assignment
requests for queued dispatch data must include that token, and the UI renews
both hold types while the relevant control remains open.

Task assets retain their format `kind` and add a grouping `category`: `code`,
`tests`, `documents`, `logs`, or `other` (the default). Asset create and all
asset/dashboard responses round-trip both fields.

## Event subscriptions

A task's subscription to an event `(source, source_key)` pair. When an ingress
event matches, the server fans out a routed copy to every subscriber task and
the canonical event settles in the `broadcast` state with `deliveries` in the
ingress response. Matching is exact; subscriptions only affect events ingested
after they exist (no replay).

```
POST   /v1/agent-tasks/{id}/event-subscriptions
       body: {"source": "poll_plugin:github_pr", "source_key": "org/repo#456"}
       → 201 {"id", "object": "agent.task.event_subscription", "task_id",
               "source", "source_key", "owner_user_id", "created_at"}
       (idempotent: re-posting the same pair returns the existing row)
GET    /v1/agent-tasks/{id}/event-subscriptions
       → {"object": "list", "data": [<subscription>, ...]}
DELETE /v1/agent-tasks/{id}/event-subscriptions/{subscription_id}
       → {"id", "object": "agent.task.event_subscription", "deleted": true}
```

## Task items

| Method | Path |
|--------|------|
| POST | `/v1/task-items/{id}/resolve` |
| PATCH | `/v1/task-items/{id}` |
| POST | `/v1/task-items/{id}/dispatch` |
| POST | `/v1/task-items/{id}/retry-dispatch` |
| POST | `/v1/task-items/{id}/cancel` |
| POST | `/v1/task-items/{id}/edit-lease` |
| DELETE | `/v1/task-items/{id}/edit-lease/{token}` |

Task items carry a `kind`: `work` (default) dispatches to a worker lane;
`human_action` is completed by the user by hand. Human action items carry only
`title` + `description` (what/why/how) — `worker_id` and `instructions` are
rejected at creation, and worker assignment is refused. The user settles one
from the task card: `POST /v1/task-items/{id}/resolve` with
`{"resolution": "mark_done"}` moves it to `done` and emits an
`item.human_action.done` event born `routed` to the task (payload:
`{"item_id", "item_title", "kind"}`), so the manager packager wakes the
manager; `reject_item` cancels it without an event. Create one via
`POST /v1/agent-tasks/{id}/items` with `kind: "human_action"`, no `worker_id`,
and `submit_for_user_ack: true`.

## Agent queues

| Method | Path |
|--------|------|
| GET | `/v1/agent-queues` |
| GET | `/v1/agent-queues/{role}/items` |
| POST | `/v1/agent-queues/{role}/pause` |
| POST | `/v1/agent-queues/{role}/resume` |
| PATCH | `/v1/agent-queue-items/{id}` |
| POST | `/v1/agent-queue-items/{id}/cancel` |

## Task workers

Workers are durable per-task handles created from Worker Providers. Creation
returns an uninitialized `worker_id` immediately. Call `initialize` to start the
target asynchronously; successful initialization records the target system's
`target_id`. Items reference Workers by `worker_id`.

| Method | Path |
|--------|------|
| GET | `/v1/worker-providers` |
| GET | `/v1/worker-providers/{provider_id}` |
| POST | `/v1/worker-providers` |
| PATCH | `/v1/worker-providers/{provider_id}` |
| DELETE | `/v1/worker-providers/{provider_id}` |
| GET | `/v1/agent-tasks/{task_id}/workers` |
| POST | `/v1/agent-tasks/{task_id}/workers` |
| POST | `/v1/agent-tasks/{task_id}/workers/assign` |
| POST | `/v1/task-workers/{worker_id}/initialize` |
| POST | `/v1/task-workers/{worker_id}/rebind` |
| POST | `/v1/task-workers/{worker_id}/interrupt` |
| DELETE | `/v1/task-workers/{worker_id}` |

## Board triage

| Method | Path |
|--------|------|
| GET | `/v1/agent-tasks/board/pending` |
| POST | `/v1/fyi-clusters/{id}/resolve` |

## Task agent roles

`{role}` is a PuppyGarden role slug. Broker, secretary, and manager roles always
run on OmniHarness and reference hidden PromptProfile manuals. Custom manager
roles are created through `roles/manager`; the seeded default manager cannot be
deleted. Worker configuration belongs to Worker Providers, not roles.

| Method | Path |
|--------|------|
| GET | `/v1/agent-tasks/roles/profiles` |
| GET | `/v1/agent-tasks/roles/{role}/profile` |
| PUT | `/v1/agent-tasks/roles/{role}/profile` |
| PUT | `/v1/agent-tasks/roles/{role}/prompt` |
| POST | `/v1/agent-tasks/roles/manager` |
| DELETE | `/v1/agent-tasks/roles/{role}` |
| POST | `/v1/agent-tasks/roles/{role}/session` |
| POST | `/v1/agent-tasks/roles/{role}/session/reset` |

## Session adoption

Internal sessions (`sessions/{session_id}`) are adopted by conversation id.
External, watcher-discovered sessions (`external-sessions/{session_hint}`) use
the hint as their target id. Watcher updates may report `activity`, `connected`,
`needs_response`, and `failure_reason`; PuppyGarden observes those fields but
does not route the user's response back to the external application.

| Method | Path |
|--------|------|
| POST | `/v1/agent-tasks/sessions/{session_id}/propose-adoption` |
| POST | `/v1/agent-tasks/sessions/{session_id}/adopt` |
| POST | `/v1/agent-tasks/sessions/{session_id}/reject-adoption` |
| POST | `/v1/agent-tasks/external-sessions/propose-adoption` |
| POST | `/v1/agent-tasks/external-sessions/{session_hint}/adopt` |
| POST | `/v1/agent-tasks/external-sessions/{session_hint}/reject-adoption` |

## Poller integration

| Method | Path |
|--------|------|
| POST | `/v1/session-watcher/update` |
| GET | `/v1/agent-tasks/script-plugins/health` |
| POST | `/v1/agent-tasks/script-plugins/health` |
| PUT | `/v1/agent-tasks/script-plugins/hosts/{host_id}/{plugin_name}` |

## Automations (scheduled tasks)

| Method | Path |
|--------|------|
| POST | `/v1/scheduled-tasks` |
| GET | `/v1/scheduled-tasks` |
| GET | `/v1/scheduled-tasks/{id}` |
| PATCH | `/v1/scheduled-tasks/{id}` |
| DELETE | `/v1/scheduled-tasks/{id}` |
| POST | `/v1/scheduled-tasks/{id}/run` |
| GET | `/v1/scheduled-tasks/{id}/runs` |

Create fields: `name`, `prompt`, `rrule`, `agent_id`, `timezone`,
`model_override`, `reasoning_effort`, `permission_mode`, `max_cost_usd`,
`workspace`, `host_id`, `catch_up`.
