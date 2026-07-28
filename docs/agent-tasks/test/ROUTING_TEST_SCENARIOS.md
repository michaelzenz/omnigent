# Routing test scenarios

Manual scenarios for exercising task-event ingress, distributor auto-route,
secretary triage, and manager reconciliation. Run against a local server
(`http://127.0.0.1:6767`) with host connected.

Use a **fresh database** (no active tasks, no open routing cards) unless a
scenario says otherwise. Adjust `repo`, agent ids, and host headers to match your
environment.

---

## Scenario 1 — CI failure + Slack follow-up on fix PR (empty DB)

**Story.** PR #123 fails CI. Later, John posts on Slack that PR #456 contains the
fix for #123 and is waiting on merge approval. There are no managed tasks yet.

**Goal.** Distributor stalls both events; secretary wakes and creates **Decisions**
board proposal(s) for the user. After the user accepts, both events route to one
new task and the manager reconciles them.

### Preconditions

1. **Empty task state** — no active tasks, no open `routing_proposed` items, no
   stalled events in `awaiting_grouping` (wipe DB or use a clean server).
2. Server and host running (`uv run omnigent server`, `uv run omnigent host`).
3. Task secretary session live (`POST /v1/agent-tasks/secretary/session`). This
   also launches the session runner when a host is connected (no separate
   `POST /v1/hosts/{hostId}/runners` step).

### Events

Post in order (replace `HOST_ID` with your registered host id). Wait for secretary
triage after event 1 before posting event 2, or post both and let the secretary
catch up in one batch.

**Event 1 — CI failure on PR #123**

```http
POST /v1/task-events
X-Omnigent-Host-Id: HOST_ID

{
  "event_type": "github.pr.checks_failed",
  "title": "PR #123 checks failed",
  "summary": "repo:acme/widgets pr:123 ci failure on main merge queue",
  "source": "test:scenario-1",
  "source_key": "acme/widgets#123",
  "source_offset": 1,
  "tags": [
    {"tag_type": "repo", "tag": "acme/widgets"},
    {"tag_type": "pr", "tag": "123"}
  ],
  "payload": {"repo": "acme/widgets", "pr_number": 123}
}
```

**Event 2 — Slack: fix PR pending approval**

```http
POST /v1/task-events
X-Omnigent-Host-Id: HOST_ID

{
  "event_type": "slack.message",
  "title": "John: PR #456 fixes #123, pending merge approval",
  "summary": "repo:acme/widgets pr:456 pr:123 slack thread:eng-releases John says PR #456 is the fix for PR #123 and is pending approval before merge",
  "source": "test:scenario-1",
  "source_key": "slack:C123:1234567890.123456",
  "source_offset": 2,
  "tags": [
    {"tag_type": "repo", "tag": "acme/widgets"},
    {"tag_type": "pr", "tag": "456"},
    {"tag_type": "thread", "tag": "eng-releases"}
  ],
  "payload": {
    "channel": "eng-releases",
    "author": "john",
    "text": "PR #456 is the fix for PR #123 — pending approval for merge"
  }
}
```

### Pass criteria

**After events are posted (before user accepts)**

- [ ] Event 1 state is `routing_proposed` (on a Decisions card), not `routed`.
- [ ] Event 2 state is `routing_proposed` on the **same** Decisions card (or secretary clearly linked both before user acts).
- [ ] Secretary received a stall wake (check secretary session or server log).
- [ ] `GET /v1/agent-tasks/board/decisions` shows one actionable card with both events (preferred) or two cards the user can tell belong together.
- [ ] Card pre-selects **new task** (`suggested_task_id` null); paused proposed task is present.
- [ ] Secretary did **not** call resolve (no task exists yet).

**After user accepts the Decisions card**

- [ ] Exactly **one** new active task is created for the incident.
- [ ] Both events are `routed` on that task.
- [ ] Manager reconcile queue lists both events.
- [ ] User sees inbox items after manager reconcile; no worker dispatched without approval.

### Failure modes to watch

- Events left in `awaiting_grouping` after secretary turn (no proposal created).
- Secretary auto-resolves to a non-existent or wrong task instead of opening a board card.
- Two unrelated Decisions cards with no link between PR #123 CI failure and PR #456 Slack follow-up.
- Secretary marks Slack message as FYI only and drops the follow-up signal.
- Duplicate tasks created when user accepts.
- Manager auto-dispatches workers without user Go on inbox items.

### Verify

```http
GET /v1/task-events/ambiguous-inbox
GET /v1/agent-tasks/board/decisions
GET /v1/agent-tasks/{task_id}/reconcile-queue
```

Check event `state`, `task_id`, and board cards after ingest, after secretary
triage, and after user accepts.
