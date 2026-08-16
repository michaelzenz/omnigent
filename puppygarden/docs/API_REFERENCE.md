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

Worker lanes are per-task sub-agent slots. A lane that has not run yet can be
re-pointed at a different worker role or activated into a live session; once a
session exists the role is fixed. Items reference lanes by `worker_id`; lanes
are created explicitly (POST) or via batch assignment (POST assign).

| Method | Path |
|--------|------|
| GET | `/v1/agent-tasks/{task_id}/workers` |
| POST | `/v1/agent-tasks/{task_id}/workers` |
| POST | `/v1/agent-tasks/{task_id}/workers/assign` |
| PATCH | `/v1/task-workers/{worker_id}` |
| POST | `/v1/task-workers/{worker_id}/activate` |

## Board triage

| Method | Path |
|--------|------|
| GET | `/v1/agent-tasks/board/pending` |
| POST | `/v1/fyi-clusters/{id}/resolve` |

## Task agent roles

`{role}` is a managed task agent role slug. Profile GET/PUT endpoints accept any
supported role (`broker`, `secretary`, `manager`, `worker`). Session bootstrap
(`session` / `session/reset`) is supported for `broker` and `secretary` only.
Custom manager and worker roles are created via the dedicated `roles/manager`
and `roles/worker` endpoints and deleted via `DELETE roles/{role}` (system roles
cannot be deleted).

| Method | Path |
|--------|------|
| GET | `/v1/agent-tasks/roles/profiles` |
| GET | `/v1/agent-tasks/roles/{role}/profile` |
| PUT | `/v1/agent-tasks/roles/{role}/profile` |
| POST | `/v1/agent-tasks/roles/{role}/import-agent` |
| PUT | `/v1/agent-tasks/roles/{role}/prompt` |
| POST | `/v1/agent-tasks/roles/manager` |
| POST | `/v1/agent-tasks/roles/worker` |
| DELETE | `/v1/agent-tasks/roles/{role}` |
| POST | `/v1/agent-tasks/roles/{role}/session` |
| POST | `/v1/agent-tasks/roles/{role}/session/reset` |

## Session adoption

Internal sessions (`sessions/{session_id}`) are adopted by conversation id.
External, watcher-discovered sessions (`external-sessions/{session_hint}`) are
adopted by the session hint the watcher reported; the broker proposes adoption
and the user accepts or rejects each hint.

| Method | Path |
|--------|------|
| POST | `/v1/agent-tasks/sessions/{session_id}/propose-adoption` |
| POST | `/v1/agent-tasks/sessions/{session_id}/adopt` |
| POST | `/v1/agent-tasks/sessions/{session_id}/reject-adoption` |
| POST | `/v1/agent-tasks/external-sessions/propose-adoption` |
| POST | `/v1/agent-tasks/external-sessions/{session_hint}/adopt` |
| POST | `/v1/agent-tasks/external-sessions/{session_hint}/reject-adoption` |
