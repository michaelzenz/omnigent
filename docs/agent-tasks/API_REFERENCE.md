# Agent tasks API reference

## Ingress (Phase 5)

`POST /v1/task-events` — ingest an external event. Auth: logged-in user or host
poller (`X-Omnigent-Host-Ambient-Id`).

Dedup key: `source` + `source_key` + `source_offset` + `event_type`.

## Task events

| Method | Path |
|--------|------|
| GET | `/v1/task-events` |
| GET | `/v1/task-events/{id}` |
| POST | `/v1/task-events` |
| POST | `/v1/task-events/{id}/resolve` |
| POST | `/v1/task-events/{id}/complete` |
| POST | `/v1/task-events/{id}/dismiss` |
| POST | `/v1/task-events/batch-resolve` |

## Agent tasks

| Method | Path |
|--------|------|
| POST | `/v1/agent-tasks` |
| GET | `/v1/agent-tasks/{id}/dashboard` |
| POST | `/v1/agent-tasks/{id}/bootstrap` |
| POST | `/v1/agent-tasks/{id}/events` |
| POST | `/v1/agent-tasks/{id}/dispatch` |
| PUT | `/v1/agent-tasks/secretary/profile` |
| POST | `/v1/agent-tasks/secretary/session` |
