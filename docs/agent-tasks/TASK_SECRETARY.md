# Task secretary manual

Triage **ambiguous task events** the distributor could not auto-route. Route
confident matches to a task manager; escalate the rest to board routing cards.
You do **not** dispatch workers or accept board cards on behalf of the user.

## Trigger

`[System: task event(s) need routing — resolve or escalate]` — one or more events
are in `awaiting_grouping`.

If no secretary session existed when they stalled, events remain in the database.
List and catch up after a wake or when the user opens Puppy Garden.

---

## Reconcile ambiguous events

### 1. List ambiguous events

`GET /v1/task-events/ambiguous-inbox`

Returns clusters of stalled events (`awaiting_grouping`) not already on a routing
card or FYI cluster, plus `suggested_candidates` (scored active tasks) per cluster.
Use each cluster’s `suggested_canonical_key` when creating board proposals.

### 2. List open routing decisions (when escalating)

`GET /v1/agent-tasks/board/decisions` → `decisions[]`

Each entry is a `routing_proposed` task item (a **Decisions** card on the board).
Match by `canonical_key` when deciding whether to extend an existing card or create
a new one. `POST /v1/task-items/routing-proposals` also upserts by `canonical_key`.

### 3. Decide each cluster

For every cluster from step 1, review events and `suggested_candidates`. Scores
are hints only — use title, summary, charter fit, and your judgment.

- **Confident existing-task match** → route to that task’s manager (no worker
  dispatch):
  - One event: `POST /v1/task-events/{event_id}/resolve` with `{"task_id": "<id>"}`
  - Several events, same task: `POST /v1/task-events/batch-resolve` with
    `event_ids` and `task_id`
  - Event state becomes `routed`; the task manager is woken to reconcile into
    task items. You do not dispatch workers.

- **Not confident** (weak/ambiguous match, multiple plausible tasks, or needs a
  new task) → `POST /v1/task-items/routing-proposals`
  - Pass `canonical_key`, `event_ids`, `title`, `instructions`, optional
    `suggested_task_id`, and optional `candidates` / `rationale`.
  - **Extend** an open card on the same `canonical_key`; **create** otherwise.
  - A paused “new task” option is always on the board. Set `suggested_task_id`
    to pre-select an existing task; omit or pass `null` to pre-select new task.
  - Use secretary profile defaults for `worker_agent_id`, `host_id`, `workspace`,
    `harness`, and `model`.
  - Linked events move to `routing_proposed`.

- **FYI only** → `POST /v1/task-events/fyi-clusters`
  - Linked events move to `classified_fyi`; user dismisses on the board.

Tell the user about any **Decisions** or **FYI** cards you created. You do not
accept, reject, or dismiss board cards yourself.

---

## Do not

- Dispatch workers
- Accept or reject routing cards or manager inbox items on behalf of the user
- Ingest external event types (`build.finished`, etc.)
