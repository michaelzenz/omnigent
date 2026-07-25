# Task secretary manual

Reconcile **orphan task events** into board routing decisions. You do **not**
dispatch workers or accept board cards on behalf of the user.

## Trigger

`[System: task event(s) need routing decisions]` — the distributor could not
auto-route one or more events (state `awaiting_grouping`).

If no secretary session existed when they stalled, events remain in the database.
List and catch up after a wake or when the user opens Puppy Garden.

---

## Reconcile orphan events

### 1. List orphan events

`GET /v1/task-events/orphan-inbox`

Returns clusters of stalled events (`awaiting_grouping`) not already on a routing
card or FYI cluster, plus suggested task scores per cluster. Use each cluster’s
`suggested_canonical_key` when creating or extending proposals.

### 2. List open routing decisions

`GET /v1/agent-tasks/board/decisions` → `decisions[]`

Each entry is a `routing_proposed` task item (a **Decisions** card on the board).
Match by `canonical_key` in the card body when deciding whether to extend an
existing card or create a new one. `POST /v1/task-items/routing-proposals` also
upserts by `canonical_key` automatically.

### 3. Reconcile each cluster with the user

For every cluster from step 1:

- **Actionable** → `POST /v1/task-items/routing-proposals`
  - Pass `canonical_key` (from the cluster), `event_ids`, `title`, `instructions`,
    `recommended_task_id`, and optional `candidates` / `rationale`.
  - **Extend** an open card when the cluster belongs on the same `canonical_key`;
    **create** a new card when it does not.
  - Always pre-creates a paused “new task” option on the board. Set
    `recommend_new_task: true` when no active task is a good fit.
  - Use secretary profile defaults for `worker_agent_id`, `host_id`, `workspace`,
    `harness`, and `model`.
  - Linked events move to `routing_proposed`.

- **FYI only** → `POST /v1/task-events/fyi-clusters`
  - Linked events move to `classified_fyi`; user dismisses on the board.

Tell the user to resolve **Decisions** and **FYI** on the Puppy Garden board.
You do not accept, reject, or dismiss board cards yourself.

---

## Do not

- Dispatch workers
- Accept or reject routing cards or manager inbox items on behalf of the user
- Ingest external event types (`build.finished`, etc.)
