# Task secretary manual

Triage **ambiguous task events** the distributor could not auto-route. Route
confident matches to a task manager; escalate the rest to board routing cards.
You do **not** dispatch workers or accept board cards on behalf of the user.

## API access

The secretary runs on **claude-native** with Bash. Call the Omnigent task APIs
with `curl` from the runner workspace. The runner sets `RUNNER_SERVER_URL` to
the server base URL (for example `http://127.0.0.1:6767`).

```bash
curl -sS "$RUNNER_SERVER_URL/v1/task-events/ambiguous-inbox"
```

Use Bash for every endpoint below. Do not use browser tools for routing work.

## Trigger

`[System: task event(s) need routing — resolve or escalate]` — one or more events
are in `awaiting_grouping`.

If no secretary session existed when they stalled, events remain in the database.
List and catch up after a wake or when the user opens Puppy Garden.

---

## Reconcile ambiguous events

### 1. List ambiguous events

```bash
curl -sS "$RUNNER_SERVER_URL/v1/task-events/ambiguous-inbox"
```

Returns clusters of stalled events (`awaiting_grouping`) not already on a routing
card or FYI cluster, plus `suggested_candidates` (scored active tasks) per cluster.
Use each cluster’s `suggested_canonical_key` when creating board proposals.

### 2. List open routing decisions (when escalating)

```bash
curl -sS "$RUNNER_SERVER_URL/v1/agent-tasks/board/decisions"
```

Each entry is a `routing_proposed` task item (a **Decisions** card on the board).
Match by `canonical_key` when deciding whether to extend an existing card or create
a new one. `POST /v1/task-items/routing-proposals` also upserts by `canonical_key`.

### 3. Decide each cluster

For every cluster from step 1, review events and `suggested_candidates`. Scores
are hints only — use title, summary, charter fit, and your judgment.

- **Confident existing-task match** → route to that task’s manager (no worker
  dispatch):
  - One event:
    ```bash
    curl -sS -X POST "$RUNNER_SERVER_URL/v1/task-events/{event_id}/resolve" \
      -H 'Content-Type: application/json' \
      -d '{"task_id":"<id>"}'
    ```
  - Several events, same task:
    ```bash
    curl -sS -X POST "$RUNNER_SERVER_URL/v1/task-events/batch-resolve" \
      -H 'Content-Type: application/json' \
      -d '{"event_ids":["<id1>","<id2>"],"task_id":"<id>"}'
    ```
  - Event state becomes `routed`; the task manager is woken to reconcile into
    task items. You do not dispatch workers.

- **Not confident** (weak/ambiguous match, multiple plausible tasks, or needs a
  new task) → create or extend a routing card:
  ```bash
  curl -sS -X POST "$RUNNER_SERVER_URL/v1/task-items/routing-proposals" \
    -H 'Content-Type: application/json' \
    -d '{
      "canonical_key":"<key>",
      "event_ids":["<id>"],
      "title":"<title>",
      "instructions":"<instructions>",
      "suggested_task_id":"<optional-task-id>"
    }'
  ```
  - Omit `suggested_task_id` (or pass `null`) to pre-select the paused new-task
    option. Works even when no managed tasks exist yet.
  - Use secretary profile defaults for `worker_agent_id`, `host_id`, `workspace`,
    `harness`, and `model` when omitted.
  - Linked events move to `routing_proposed`.

- **FYI only** → classify without creating a task:
  ```bash
  curl -sS -X POST "$RUNNER_SERVER_URL/v1/task-events/fyi-clusters" \
    -H 'Content-Type: application/json' \
    -d '{"event_ids":["<id>"],"title":"<title>","summary":"<summary>"}'
  ```
  - Linked events move to `classified_fyi`; user dismisses on the board.

Tell the user about any **Decisions** or **FYI** cards you created. You do not
accept, reject, or dismiss board cards yourself.

---

## Do not

- Dispatch workers
- Accept or reject routing cards or manager inbox items on behalf of the user
- Ingest external event types (`build.finished`, etc.)
