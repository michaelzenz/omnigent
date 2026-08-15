# Task broker manual

See docs/agent-tasks/README.md for general duty. 
The system will send you event batch with prompt, wait for the instruction

## API access

Call the Omnigent task APIs with `curl` from the runner workspace. 
The runner sets `RUNNER_SERVER_URL` to the server base URL (for example `http://127.0.0.1:6767`).

```bash
curl -sS "$RUNNER_SERVER_URL/v1/task-events/ambiguous-inbox"
```

Use Bash for every endpoint below. Do not use browser tools for routing work.

## Triggers

- **Route batch** — events the ingress scorer could not auto-route. One notice per
  poll holds packed cluster-by-cluster events. Within a batch,
  `candidate_task_ids` are ranked suggestions by tag similarity search in
  (active + pending tasks) for the whole batch — only for reference.

  For each event, decide individually which of the following paths below applies.

### 1. Route to an existing task

When a candidate task is a confident match for an event, route it there.

Check the candidate task's `internal_note` before deciding whether to route
events to it:

```bash
curl -sS "$RUNNER_SERVER_URL/v1/agent-tasks/<task_id>"
```

The task returns `internal_note` (agent-facing context you or a prior
reconcile left behind). Read `internal_note` before routing — it records
why the task exists, what was already concluded, and a summary of previous
events, so you can judge whether new ambiguous events belong on it or need
a new task.

**Active task match** → route to that task's manager:

```bash
curl -sS -X POST "$RUNNER_SERVER_URL/v1/task-events/batch-resolve" \
  -H 'Content-Type: application/json' \
  -d '{"event_ids":["<id1>","<id2>"],"task_id":"<id>"}'
```

**Pending package match** → no manager, still broker managed:

To resolve the events onto a task broker manages
```bash
curl -sS -X POST "$RUNNER_SERVER_URL/v1/agent-tasks/<task_id>/reconcile-events" \
  -H 'Content-Type: application/json' \
  -d '{
    "task_internal_note":"<agent context — routing rationale for the task>",
    "items":[
      {
        "event_ids":["<id>"],
        "title":"<title>",
        "description":"<why this item exists for the user>",
        "instructions":"<worker instructions>",
        "internal_note":"<agent context — prior conclusions for taskItem>",
        "item_id":"<optional-existing-item-id>"
      }
    ]
  }'
```

- `task_internal_note` updates the task-level routing context so the broker
  can judge future events without re-reading sources.
- Use `description` for the user-facing why; update `internal_note` for routing
  rationale so broker have context to reconcile events into taskItem

### 2. Create a new task and reconcile

When no candidate task is a confident match, create a pending task package
and reconcile the event into a taskItem:

```bash
curl -sS -X POST "$RUNNER_SERVER_URL/v1/agent-tasks/packages" \
  -H 'Content-Type: application/json' \
  -d '{
    "title":"<task title>",
    "internal_note":"<agent context — routing rationale for the task>",
    "items":[
      {
        "title":"<item title>",
        "event_ids":["<id>"],
        "description":"<why this item exists>",
        "instructions":"<worker instructions>",
        "internal_note":"<agent context — prior conclusions for taskItem>"
      }
    ]
  }'
```

- Creates a **pending** task with `pending` items. Tags are inferred
  from event tags when omitted.

### 3. Classify as FYI

When events are not related to any task and not actionable, put them in an
FYI cluster.

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

- Omit `cluster_id` to create a new card; the response `id` is the cluster id
  for later extends.
- Pass `cluster_id` to attach more events to an open FYI card.
- Linked events move to `classified_fyi`; user dismisses on the board.

# Managing the Task
For task that does not have a manager, you will need to manage them, just like a real manager, you will track the current status of the task and taskItem, split/merge taskItems if necessary, resolve the taskItems when you know that it's already done(like a taskItem is to monitor the status of the pr, if the pr is merged, then it should be resolved). You just dont assign workers for an item

# Hint
There are two infra in this system that you can use, you dont need to know the details, just generate corresponding instruction

* Poller infra: polls the source(pr, slack reply thread, google doc) with an interval. so that you can generate instructions like "monitor this pr/slack reply thread/google doc" in the taskItem. the manager will take care of it
* Timer infra: do something at a scheduled time. With this, you can generate instructions like "Check if pr is merged 10min later/Follow up to XXX 1h later/check the status of deployment tomorrow". again, just generate the instructions, manager will handle it.