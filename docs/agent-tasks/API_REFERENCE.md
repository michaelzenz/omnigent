# Agent tasks API reference

All paths are under `/v1`. Auth: logged-in user unless noted.

## Ingress

`POST /v1/task-events` — ingest an external event. Auth: logged-in user or host
poller (`X-Omnigent-Host-Ambient-Id`).

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
| GET | `/v1/agent-tasks/{id}` |
| PATCH | `/v1/agent-tasks/{id}` |
| DELETE | `/v1/agent-tasks/{id}` |
| PUT | `/v1/agent-tasks/{id}/tags` |
| GET | `/v1/agent-tasks/{id}/executions` |
| POST | `/v1/agent-tasks/{id}/bootstrap` |
| GET | `/v1/agent-tasks/{id}/dashboard` |
| GET | `/v1/agent-tasks/{id}/items` |
| POST | `/v1/agent-tasks/{id}/items` |
| GET | `/v1/agent-tasks/{id}/reconcile-queue` |
| POST | `/v1/agent-tasks/{id}/ack` |
| POST | `/v1/agent-tasks/{id}/reconcile-events` |
| POST | `/v1/agent-tasks/{id}/reject-package` |

## Task items

| Method | Path |
|--------|------|
| POST | `/v1/task-items/{id}/resolve` |
| PATCH | `/v1/task-items/{id}` |
| POST | `/v1/task-items/{id}/dispatch` |

## Board triage

| Method | Path |
|--------|------|
| GET | `/v1/agent-tasks/board/pending` |
| POST | `/v1/fyi-clusters/{id}/resolve` |

## Task agent roles

`{role}` is a managed task agent role slug. Profile endpoints accept any
supported role (`broker`, `secretary`); session bootstrap is supported for
both roles.

| Method | Path |
|--------|------|
| GET | `/v1/agent-tasks/roles/{role}/profile` |
| PUT | `/v1/agent-tasks/roles/{role}/profile` |
| POST | `/v1/agent-tasks/roles/{role}/session` |
| POST | `/v1/agent-tasks/roles/{role}/session/reset` |

## Session adoption

| Method | Path |
|--------|------|
| POST | `/v1/agent-tasks/sessions/{session_id}/propose-adoption` |
| POST | `/v1/agent-tasks/sessions/{session_id}/adopt` |
| POST | `/v1/agent-tasks/sessions/{session_id}/reject-adoption` |

## Timer items

Agent-created deferred work executed by a registered host. Each row is scoped
to one `host_id`; the host only pulls due items for itself.

| Method | Path | Auth |
|--------|------|------|
| POST | `/v1/timer-items` | user |
| GET | `/v1/timer-items/due` | host (`X-Omnigent-Host-Ambient-Id`) |
| POST | `/v1/timer-items/{id}/claim` | host |
| POST | `/v1/timer-items/{id}/complete` | host |
| POST | `/v1/timer-items/{id}/fail` | host |
| POST | `/v1/timer-items/dispatch-prompt` | host |

Create body: `{ "task_type", "fire_at", "host_id", "payload" }`.

First handler: `task_type: "prompt"` with payload
`{ "session_id": "...", "message": "..." }`.
