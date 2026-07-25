# Managed agent tasks

Long-lived **tasks** are owned by a **task manager** agent. External **events**
enter via `POST /v1/task-events`, are scored and routed by the **distributor**,
and are reconciled into **task items** (the manager backlog and Puppy Garden inbox).

## Layers

`Task` → `TaskEvent` (signals) → `TaskItem` (backlog/inbox) → `TaskEventExecution` (worker runs)

## Roles

| Role | Manual |
|------|--------|
| User / operator | [USER_GUIDE.md](USER_GUIDE.md) |
| Task manager | [TASK_MANAGER.md](TASK_MANAGER.md) |
| Task secretary | [TASK_SECRETARY.md](TASK_SECRETARY.md) (includes orphan session routing search text) |
| Task worker | [TASK_WORKER.md](TASK_WORKER.md) |
| Poll plugin author | [TASK_POLL_AUTHOR.md](TASK_POLL_AUTHOR.md) (see [POLL_PLUGINS.md](POLL_PLUGINS.md)) |

## Routing thresholds

Auto-route when **both** hold:

- Top task confidence ≥ `0.6`
- Margin over second place ≥ `0.15`

Otherwise the event stalls for secretary/user resolution.

## Event states (routing)

`received` → `routing` → `awaiting_user_selection` | `awaiting_grouping` →
`grouping_proposed` → `routed` → `reconciled` | `dismissed` | `failed`

Session adoption uses `awaiting_user_ack` (separate lane).

## Task item states

`draft` → `routing_proposed` (secretary routing card) → `running` / `cancelled`

`draft` → `awaiting_user_ack` (inbox) → `approved` → `queued` / `running` / `done` / `cancelled`

## API

See [API_REFERENCE.md](API_REFERENCE.md). Phase 5 adds `POST /v1/task-events` ingress.

## Session adoption (Phase 5.5)

[SESSION_ADOPTION.md](SESSION_ADOPTION.md) — orphan sessions stay unbound until the
user accepts a secretary proposal. Secretary writes routing search text, proposes a
task match; **no auto-adopt** for sessions.

Event ingress uses auto-route thresholds; **session adoption does not**.

## Poll plugins

[POLL_PLUGINS.md](POLL_PLUGINS.md) — agent-authored host scripts under
`$OMNIGENT_DATA_DIR/poll_plugins/<name>/run.py` emit task events without host
restarts. The [poll plugin author](TASK_POLL_AUTHOR.md) role creates and maintains them.
