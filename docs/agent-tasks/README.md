# Managed agent tasks

Long-lived **tasks** are owned by a **task manager** agent. External **events**
enter via `POST /v1/task-events`, are scored and routed by the **distributor**,
and land in `awaiting_manager_triage` for manager assessment.

## Roles

| Role | Manual |
|------|--------|
| User / operator | [USER_GUIDE.md](USER_GUIDE.md) |
| Task manager | [TASK_MANAGER.md](TASK_MANAGER.md) |
| Task secretary | [TASK_SECRETARY.md](TASK_SECRETARY.md) |
| Task worker | [TASK_WORKER.md](TASK_WORKER.md) |

## Routing thresholds

Auto-route when **both** hold:

- Top task confidence ≥ `0.6`
- Margin over second place ≥ `0.15`

Otherwise the event stalls for secretary/user resolution.

## Event states (routing)

`received` → `routing` → `awaiting_manager_triage` | `awaiting_user_selection` |
`awaiting_new_manager_decision` → `processed` | `dismissed`

Manager proposals use `awaiting_user_ack` (separate lane).

## API

See [API_REFERENCE.md](API_REFERENCE.md). Phase 5 adds `POST /v1/task-events` ingress.

## Deferred

Orphan session adoption (`binding_kind=ambient`) is planned for a follow-up phase.
