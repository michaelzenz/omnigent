# Task secretary manual


## API access

Call the Omnigent task APIs with `curl` from the runner workspace. 
The runner sets `RUNNER_SERVER_URL` to the server base URL (for example `http://127.0.0.1:6767`).

```bash
curl -sS "$RUNNER_SERVER_URL/v1/task-events/ambiguous-inbox"
```

Use Bash for every endpoint below. Do not use browser tools for routing work.

## Trigger

`[System: task event(s) need routing — resolve or escalate]` — one or more events
are in `awaiting_grouping`.

---

## Reconcile ambiguous events

We want to avoid user make decisions on too many things, so we should reconcile TaskEvents(rawEvents)
into spcific taskItem as execution unit

### 1. List ambiguous events

```bash
curl -sS "$RUNNER_SERVER_URL/v1/task-events/ambiguous-inbox"
```

Returns clusters of stalled events (`awaiting_grouping`) grouped by shared event tags.
Use each cluster’s `tags` and `suggested_candidates` when deciding how to route.
Candidates include both **active** and **paused** tasks.

Optional: rank tasks for specific events:

```bash
curl -sS -X POST "$RUNNER_SERVER_URL/v1/task-events/match-tasks" \
  -H 'Content-Type: application/json' \
  -d '{"event_ids":["<id1>","<id2>"]}'
```

### 2. List pending task packages (when escalating)

```bash
curl -sS "$RUNNER_SERVER_URL/v1/agent-tasks/board/pending"
```

Each `pending` entry is a **paused** task with inbox items awaiting user acknowledgment.
To add more events to an existing package, call
`POST /v1/agent-tasks/{task_id}/reconcile-events` with `item_id` to extend an item.

### 3. Decide each cluster

For every cluster from step 1, review events and `suggested_candidates`. Scores
are hints only — use title, summary, charter fit, and your judgment.

- **Confident existing active-task match** → route to that task’s manager (no worker
  dispatch) with `POST /v1/task-events/batch-resolve` (use one or more
  `event_ids`):
  ```bash
  curl -sS -X POST "$RUNNER_SERVER_URL/v1/task-events/batch-resolve" \
    -H 'Content-Type: application/json' \
    -d '{"event_ids":["<id1>","<id2>"],"task_id":"<id>"}'
  ```
  - Event state becomes `routed`; the task manager is woken to reconcile into
    task items. You do not dispatch workers.

- **Confident existing paused-package match** → reconcile onto that paused task:
  ```bash
  curl -sS -X POST "$RUNNER_SERVER_URL/v1/agent-tasks/<task_id>/reconcile-events" \
    -H 'Content-Type: application/json' \
    -d '{
      "event_ids":["<id>"],
      "title":"<title>",
      "instructions":"<instructions>",
      "item_id":"<optional-existing-item-id>"
    }'
  ```
  - Events move to `reconciled`; items stay in `awaiting_user_ack` until the user
    accepts the package on the board.

- **Not confident** (weak/ambiguous match, multiple plausible tasks, or needs a
  new task) → create a paused task package:
  ```bash
  curl -sS -X POST "$RUNNER_SERVER_URL/v1/agent-tasks/packages" \
    -H 'Content-Type: application/json' \
    -d '{
      "title":"<task title>",
      "items":[
        {
          "title":"<item title>",
          "event_ids":["<id>"],
          "instructions":"<instructions>"
        }
      ]
    }'
  ```
  - Creates a **paused** task with `awaiting_user_ack` items. Tags are inferred
    from event tags when omitted.
  - Pass `item_id` on `reconcile-events` to attach more events to an open package item.

- **FYI only** → classify without creating a task:
  ```bash
  curl -sS -X POST "$RUNNER_SERVER_URL/v1/task-events/fyi-clusters" \
    -H 'Content-Type: application/json' \
    -d '{"event_ids":["<id>"],"headline":"<headline>","cluster_id":"<optional-existing-cluster-id>"}'
  ```
  - Pass `cluster_id` to attach more events to an open FYI card.
  - Linked events move to `classified_fyi`; user dismisses on the board.

Tell the user about any **pending packages** or **FYI** cards you created. You do not
accept, reject, or dismiss board cards yourself.

---

## Do not

- Dispatch workers
- Accept or reject task packages or manager inbox items on behalf of the user
- Ingest external event types (`build.finished`, etc.)
