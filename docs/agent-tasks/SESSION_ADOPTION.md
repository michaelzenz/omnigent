# Orphan session adoption (Phase 5.5)

**Goal:** Offer to route imported and user-started sessions into the task system.
**Orphan sessions are allowed** — a session stays unbound until the user accepts
a broker adoption proposal. Reject → no manager involvement.

**Migration:** Deferred. Reuse existing event states and conversation labels.

## Design decisions (locked)

| Topic | Decision |
|-------|----------|
| Orphan default | Sessions stay orphan until user **accepts** adoption |
| Auto-adopt | **Never** for sessions — always broker propose → user accept |
| Routing tags | Broker writes `omnigent.task.routing_repo` (and optional `routing_intent`) before scoring |
| User reject | No binding; optional `omnigent.task.adoption_dismissed=1` label |
| Host imports | `host_id` → `host.owner` → that user's broker |
| User sessions | Same pipeline as host import |
| Broker wake | **Batch/debounced** for import bursts |
| No broker session | Queue pending; process when broker session starts |
| After accept | `POST …/sessions/{id}/adopt` → task binding + `session.adopted` → manager triage |

## Pipeline overview

```mermaid
flowchart TB
  Trigger[Import or create session] --> Batch[Batch wake broker]
  Batch --> Tags[Broker writes routing tags]
  Tags --> Score[Score vs active tasks]
  Score --> Propose[Broker proposes adoption to user]
  Propose --> User{User}
  User -->|accept| Adopt[POST …/sessions/id/adopt]
  User -->|reject| Orphan[Stays orphan]
  Adopt --> Bind[task binding]
  Bind --> Event[session.adopted]
  Event --> Manager[routed]
```

1. **Detect** orphan (no binding; not task-spawned `manager`/`worker`).
2. **Batch-wake broker** for the session owner (`host.owner` or session user).
3. **Broker writes routing tags** (see [TASK_BROKER.md](TASK_BROKER.md)).
4. **Score** tasks using that text (thresholds inform the proposal, not auto-adopt).
5. **Broker proposes** which task should adopt the session (top match + alternatives).
6. **User accepts** → `POST /v1/agent-tasks/sessions/{session_id}/adopt`.
7. **User rejects** → session remains orphan; set dismiss label to avoid re-prompting.

Scoring thresholds (≥ `0.6`, margin ≥ `0.15`) rank candidates for the proposal;
they do **not** auto-bind sessions.

## Routing tag storage

| Label key | Purpose |
|-----------|---------|
| `omnigent.task.routing_repo` | Repo tag for task scoring (`repo` dimension) |
| `omnigent.task.routing_intent` | Optional intent tag (`intent` dimension) |
| `omnigent.task.adoption_dismissed` | Set when user rejects adoption (`1`) |

## Event type

`session.adopted` — created only after user accepts. Links via `source_session_id`.
Lands in `routed` for manager reconciliation into task items.

## Hooks

| Hook | When |
|------|------|
| `POST /v1/imports` success | Poller path; owner = `host.owner` |
| `POST /v1/sessions` create | User-started Omnigent sessions |
| `POST /v1/agent-tasks/roles/{role}/session` | Flush queued orphan sessions |

Excluded: task-spawned sessions (`bootstrap`, `dispatch`).

## API

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/v1/agent-tasks/sessions/{session_id}/propose-adoption` | Broker scores tasks and creates proposal |
| POST | `/v1/agent-tasks/sessions/{session_id}/adopt` | User accepted; bind + wake manager |
| POST | `/v1/agent-tasks/sessions/{session_id}/reject-adoption` | User rejected; stay orphan |

Broker may also use resolve-style flows on a `session.adoption` proposal event
if we model proposals as task events (implementation choice).

## After adoption

Events with `source_session_id` use the Phase 5 ingress binding fast-path.
