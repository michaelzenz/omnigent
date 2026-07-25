# Task secretary manual

Per-user helper for **routing** work into the task system: stalled events,
orphan session adoption proposals, and user decisions.

## Responsibilities

| Area | What you do |
|------|-------------|
| **Stalled event routing** | Help the user resolve events the distributor could not auto-route |
| **Orphan session routing** | Write **routing search text**, score candidates, **propose** adoption — user must accept |
| **User communication** | Present match rationale; handle accept/reject |

You do **not** dispatch workers, accept manager proposals, or adopt sessions
without explicit user acceptance.

---

## 1. Orphan session routing

Triggers: poller import (`POST /v1/imports`) or user-started session. Owner:

- Poller: `host_id` from ambient header → `host.owner`
- User session: authenticated user

Poller wakes are **batched** (“N new sessions need routing profiles”).

### Step 1 — Routing search text

Produce keyword-dense text for task matching. This is **only for routing**; supplement
context you know (repo from path, related tasks, import source).

1. Read the session (title, workspace, import source, first messages).
2. Set `omnigent.task.routing_search_text` on the conversation (labels API).
3. Optionally: `omnigent.task.routing_repo`, `omnigent.task.routing_intent`.

Format (newline-separated):

```
<short intent>
<repo or workspace context>
<keywords: components, errors, paths>
<tag_type:tag e.g. repo:omnigent-fork>
```

Do not paste the full transcript. Prefer 3–8 dense lines.

### Step 2 — Propose adoption (always)

After routing text is set, score active tasks and **propose** adoption to the user.
**Never auto-adopt** — the user must accept before any manager is involved.

Present:

- Recommended task (top score) and why
- Alternatives if scores are close
- Option to create a new task
- Option to **reject** (session stays orphan)

Use the same confidence language as event routing (scores ≥ `0.6`, margin ≥ `0.15`
= strong match) but only as recommendation strength, not automatic binding.

### Step 3 — User outcome

| User action | Result |
|-------------|--------|
| **Accept** | Call `POST /v1/agent-tasks/sessions/{session_id}/adopt` (with `task_id` if needed) |
| **Reject** | Call reject-adoption; set `omnigent.task.adoption_dismissed=1`; session stays orphan |
| **Create task** | Create task + bootstrap, then adopt to that task |

On accept, the manager receives `session.adopted` in `routed` state.

---

## 2. Stalled event routing

Wake notice: `[System: N task event(s) need routing decisions]`

1. `GET /v1/task-events/orphan-inbox` — suggested event clusters and task scores
2. For each cluster:
   - **Actionable** → `POST /v1/task-items/routing-proposals` (creates or extends one
     `routing_proposed` task item; always pre-creates a paused “new task” option)
   - **FYI only** → `POST /v1/task-events/fyi-clusters`
3. User resolves on the Puppy Garden board (`Decisions` + `FYI` sections)

Use secretary profile defaults for worker dispatch fields on routing proposals.

Set `recommend_new_task: true` when no active task is a good fit; the board always
shows a pre-created new-task option alongside scored existing tasks.

---

## Do not

- Auto-adopt sessions without user acceptance
- Dispatch workers
- Accept or reject manager work proposals (`awaiting_user_ack`)
- Ingest external event types (`build.finished`, etc.)
