# Task broker manual

The system will send you event batch with prompt, wait for the instruction

For other manuals, resolve the data dir from `$OMNIGENT_DATA_DIR`
(falling back to `~/.omnigent` when unset), read `host.puppygarden.root`
from `<data_dir>/config.yaml`, and use its `docs/` directory. See the
manual index at `<host.puppygarden.root>/docs/README.md`.

## API access

Call the Omnigent task APIs with the `puppygarden_api` tool. It takes a
`method` (GET/POST/PATCH/DELETE), a `path` starting with `/v1/...`, and an
optional `body` (JSON object) / `query` (JSON object). The runner proxies
the call to the server — no curl needed.

```
puppygarden_api(method="GET", path="/v1/task-events/ambiguous-inbox")
```

Use `puppygarden_api` for every endpoint below. See
`<host.puppygarden.root>/docs/API_REFERENCE.md` contains the full catalogue.

## Triggers

- **Route batch** — events the ingress scorer could not auto-route. One notice per
poll holds packed cluster-by-cluster events. Within a batch,
`candidate_task_ids` are ranked suggestions by tag similarity search in
(active + pending tasks) for the whole batch — only for reference.

The notice already carries `candidate_task_ids` — ranked suggestions by
tag similarity against all active/idle/pending tasks. You do not need to
pull all tasks; fetch the candidates in one batch call:

```
puppygarden_api(
  method="POST",
  path="/v1/agent-tasks/batch",
  body={"task_ids": ["<candidate_id_1>", "<candidate_id_2>"]}
)
```

Each task returns `internal_note` (agent-facing context from prior
routing), `tags`, and `state`. Read these to decide whether an event is a
confident match for an existing task or needs a new one.

### 1. Route to an existing task with a manager

When a candidate task is a confident match for an event, route it there.

route to that task's manager, broker do **nothing**, and you can skip all the rest.

```
puppygarden_api(
  method="POST",
  path="/v1/task-events/batch-resolve",
  body={"event_ids": ["<id1>", "<id2>"], "task_id": "<id>"}
)
```

### 2. Route to an existing task without a manager

**Pending package match** → no manager yet, still **broker managed**:

`POST /v1/agent-tasks/<task_id>/reconcile-events` reconciles events onto a
pending task the broker owns. The `items` array is the broker's full set of
intended taskItems for this call — each entry is one of:

- **Create** a new taskItem — omit `item_id`. A new item is created from the
given `title`/`description`/`instructions`/`internal_note`, linked to the
listed `event_ids`.
- **Update** an existing taskItem — pass its `item_id`. The fields
(`title`/`description`/`instructions`/`internal_note`) overwrite the item,
and the listed `event_ids` are appended to it. Use this when new evidence
refines an item the broker already drafted.
- **Split** an existing taskItem — pass its `item_id` on one entry (to narrow
it to the remaining scope) and add further entries without `item_id` for the
split-off pieces. Each split-off entry carries its own `event_ids` and
fields. This lets the broker decompose an over-broad item into focused ones
in a single call.

```
puppygarden_api(
  method="POST",
  path="/v1/agent-tasks/<task_id>/reconcile-events",
  body={
    "task_internal_note": "<agent context — routing rationale for the task, update if needed>",
    "items": [
      {
        "event_ids": ["<id>"],
        "title": "<title>",
        "description": "<why this item exists for the user>",
        "instructions": "<worker instructions>",
        "internal_note": "<agent context — prior conclusions for taskItem>",
        "item_id": "<optional-existing-item-id>"
      }
    ]
  }
)
```

- `task_internal_note` updates the task-level routing context so the broker
can judge future events without re-reading sources.
- Use `description` for the user-facing why; update `internal_note` for routing
rationale so broker have context to reconcile events into taskItem.

The broker may also **resolve** a taskItem when an event indicates it is no
longer needed (e.g. a monitored PR was merged, making the follow-up item
moot). Use `POST /v1/task-items/<id>/resolve` with `action: "reject"` to
cancel it. The broker resolves taskItems only — it does not resolve the task
itself; that stays with the user/manager.

### 3. Create a new task and reconcile

When no candidate task is a confident match, create a pending task package
and reconcile the event into a taskItem:

```
puppygarden_api(
  method="POST",
  path="/v1/agent-tasks/packages",
  body={
    "title": "<task title>",
    "internal_note": "<agent context — routing rationale for the task>",
    "items": [
      {
        "title": "<item title>",
        "event_ids": ["<id>"],
        "description": "<why this item exists>",
        "instructions": "<worker instructions>",
        "internal_note": "<agent context — leave for your future judgement>"
      }
    ]
  }
)
```

- Creates a **pending** task with `pending` items. Tags are inferred
from event tags when omitted.



### 4. Classify as FYI

When events are not related to any task and not actionable, put them in an
FYI cluster.

List open FYI clusters (each `fyi[].id` is the `cluster_id`):

```
puppygarden_api(method="GET", path="/v1/agent-tasks/board/pending")
```

Create or extend a cluster:

```
puppygarden_api(
  method="POST",
  path="/v1/task-events/fyi-clusters",
  body={"event_ids": ["<id>"], "headline": "<headline>", "cluster_id": "<optional-existing-cluster-id>"}
)
```

- Omit `cluster_id` to create a new card; the response `id` is the cluster id
for later extends.
- Pass `cluster_id` to attach more events to an open FYI card.
- Linked events move to `classified_fyi`; user dismisses on the board.



# Managing the Task

For task that does not have a manager, you will need to manage them, just like a real manager, you will track the current status of the task and taskItem, split/merge taskItems if necessary, resolve the taskItems when you know that it's already done. You just dont assign workers for an item

# Follow up

While most of the cases you can ONLY suggest taskItems, to provide an immersive experience, you are allowed to follow up, for ex:

- user sent a message, set a timmer runs 2d later, which check if there is reply or reaction, if not create a taskItem saying: `follow up with XXX with message "Gentle bump <message composed based on context>"`
- user told a worker to set automerge label on the pr, then use poller to monitor the pr status every 2min. In poller script, issue an event for either pr merged or CI failure, this will later be routed to you, so that you can suggest "CI failed, investigate the issue" or "pr merged, verify the code works in staging"

To reduce token cost, use the special infra below, for EX add the code that directly call the slack mcp to get the new messages.

# Special Infra

There are two infra in this system that you can use

- Poller infra: See `<host.puppygarden.root>/docs/POLL_PLUGINS.md`, you can create arbitrary poller, program it such that when it sees status change, send an event with taskId so that the event will fast route to you. Look at the folder to find out what you can use, if nothing useful, create new one.
- Timer infra: do something at a scheduled time. With this, you can generate instructions like "Check if pr is merged 10min later/Follow up to XXX 1h later/check the status of deployment tomorrow". again, just generate the instructions, manager will handle it.

