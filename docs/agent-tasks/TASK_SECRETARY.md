# Task secretary manual

You mainly handles taskEvents.

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

We first find the right task for the taskEvent, because task has richer info. Then we reconcile events
into existing/new taskItem as execution unit, so that we can avoid user make decisions on too many things.

### 1. List ambiguous events

```bash
curl -sS "$RUNNER_SERVER_URL/v1/task-events/ambiguous-inbox"
```

Returns clusters of stalled events (`awaiting_grouping`) grouped by shared event tags.
Use each cluster’s `tags` and `suggested_candidates` when deciding how to route.
Candidates include both **active** and **pending** tasks.

### 2. List routable tasks (active and pending)

rank tasks for specific events:
This api returns task candidates for the input group, usually used for finding candidate
for a group of close events, so that you can pick from smaller subset of candidates(limit to 5 for now)

```bash
curl -sS -X POST "$RUNNER_SERVER_URL/v1/task-events/match-tasks" \
  -H 'Content-Type: application/json' \
  -d '{"event_ids":["<id1>","<id2>"]}'
```

List items on a package (use `item_id` when extending one):

```bash
curl -sS "$RUNNER_SERVER_URL/v1/agent-tasks/<task_id>/items"
```

Each item returns `internal_note` (agent-facing context you or a prior
reconcile left behind). Read `internal_note` before extending an item — it
records why that item exists, what was already concluded, and summary of previous events,
so you can judge whether new ambiguous events belong on it or need a
new item.

Optional filter, e.g. inbox items only: `?state=awaiting_user_ack`.

### 3. Decide each cluster

- **Confident existing active-task match** → route to that task’s manager (no worker
  dispatch) with `POST /v1/task-events/batch-resolve` (use one or more
  `event_ids`):
  ```bash
  curl -sS -X POST "$RUNNER_SERVER_URL/v1/task-events/batch-resolve" \
    -H 'Content-Type: application/json' \
    -d '{"event_ids":["<id1>","<id2>"],"task_id":"<id>"}'
  ```
  - route to the active task means you dont need to reconcile to taskItem

- **Confident existing pending-package match** → reconcile onto that pending task:
  ```bash
  curl -sS -X POST "$RUNNER_SERVER_URL/v1/agent-tasks/<task_id>/reconcile-events" \
    -H 'Content-Type: application/json' \
    -d '{
      "event_ids":["<id>"],
      "title":"<title>",
      "description":"<why this item exists for the user>",
      "instructions":"<worker instructions>",
      "internal_note":"<agent context — links, ids, prior conclusions>",
      "item_id":"<optional-existing-item-id>"
    }'
  ```
  - This case you need to reconcile into taskItem. Update title, description, or
    instructions if needed. Use `description` for the user-facing why; put source
    excerpts and routing rationale in `internal_note` such that you or other agent can have
    enough context and avoid pulling from source for full context as much as possible in the future.
  - Events move to `reconciled`; items stay in `awaiting_user_ack` until the user
    hits **Go** on the inbox item on the board.

- **Not confident** (weak/ambiguous match and needs new task) → create a pending task package:
  ```bash
  curl -sS -X POST "$RUNNER_SERVER_URL/v1/agent-tasks/packages" \
    -H 'Content-Type: application/json' \
    -d '{
      "title":"<task title>",
      "items":[
        {
          "title":"<item title>",
          "event_ids":["<id>"],
          "description":"<why this item exists>",
          "instructions":"<worker instructions>",
          "internal_note":"<agent context>"
        }
      ]
    }'
  ```
  - Creates a **pending** task with `awaiting_user_ack` items. Tags are inferred
    from event tags when omitted.
  - Pass `item_id` on `reconcile-events` to attach more events to an open package item.

- **FYI only** → classify without creating a task:

  List open FYI clusters (each `fyi[].id` is the `cluster_id`):

  ```bash
  curl -sS "$RUNNER_SERVER_URL/v1/agent-tasks/board/pending"
  ```

  Create or extend a cluster:

  ```bash
  curl -sS -X POST "$RUNNER_SERVER_URL/v1/task-events/fyi-clusters" \
    -H 'Content-Type: application/json' \
    -d '{"event_ids":["<id>"],"headline":"<headline>","cluster_id":"<optional-existing-cluster-id>"}'
  ```
  - When you think these events are NOT related to any task, and not actionable, just fyi
  - Omit `cluster_id` to create a new card; the response `id` is the cluster id for later extends
  - Pass `cluster_id` to attach more events to an open FYI card
  - Linked events move to `classified_fyi`; user dismisses on the board
