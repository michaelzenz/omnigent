# Agent tasks API reference

All paths are under `/v1`. Auth: logged-in user unless noted.

## Ingress

`POST /v1/task-events` — ingest an external event. Auth: logged-in user or host
poller (`X-Omnigent-Host-Id`).

Dedup key: `source` + `source_key` + `source_offset` + `event_type`.

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
| DELETE | `/v1/agent-tasks/{id}` |
| PUT | `/v1/agent-tasks/{id}/tags` |
| GET | `/v1/agent-tasks/{id}/executions` |
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

## Task items

| Method | Path |
|--------|------|
| POST | `/v1/task-items/{id}/resolve` |
| PATCH | `/v1/task-items/{id}` |
| POST | `/v1/task-items/{id}/dispatch` |

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
