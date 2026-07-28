# Routing test scenarios

Manual scenarios for exercising task-event ingress, distributor auto-route,
secretary triage, and manager reconciliation. Run against a local server
(`http://127.0.0.1:6767`) with host connected.

Use a **fresh database** (no active tasks, no open packages) unless a
scenario says otherwise. Adjust `repo`, agent ids, and host headers to match your
environment.

---

## Scenario 1 — CI failure + Slack follow-up on fix PR (empty DB)

**Story.** PR #123 fails CI. Later, John posts on Slack that PR #456 contains the
fix for #123 and is waiting on merge approval. There are no managed tasks yet.

**Goal.** Distributor stalls both events; secretary wakes and creates a **paused
task package** for the user. After the user accepts the package, the task becomes
active and the manager reconciles routed work.

### Preconditions

1. **Empty task state** — no active tasks, no open paused packages, no
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

- [ ] Event 1 state is `reconciled` on a paused package item, not `routed`.
- [ ] Event 2 is `reconciled` on the **same** package item (or secretary clearly linked both before user acts).
- [ ] Secretary received a stall wake (check secretary session or server log).
- [ ] `GET /v1/agent-tasks?state=paused` shows one paused task with both events on inbox items (preferred) or two packages the user can tell belong together.
- [ ] Package task state is `paused` with `awaiting_user_ack` inbox items.
- [ ] Secretary did **not** bootstrap a manager session yet.

**After user Go on an inbox item**

- [ ] Exactly **one** active task exists for the incident (first Go activates the package).
- [ ] Both events remain `reconciled` on that task.
- [ ] Go dispatches a worker for that item; remaining inbox items stay until the user acts.
- [ ] Manager session is bootstrapped on first Go; no worker dispatched without user Go.

**After user skips every inbox item**

- [ ] Paused task remains on the board (no auto-archive).

### Failure modes to watch

- Events left in `awaiting_grouping` after secretary turn (no package created).
- Secretary auto-resolves to a non-existent or wrong active task instead of opening a package.
- Two unrelated packages with no link between PR #123 CI failure and PR #456 Slack follow-up.
- Secretary marks Slack message as FYI only and drops the follow-up signal.
- Duplicate tasks created when user Go on multiple items without reconciling first.
- Manager auto-dispatches workers without user Go on inbox items.

### Verify

```http
GET /v1/task-events/ambiguous-inbox
GET /v1/agent-tasks?state=paused
GET /v1/agent-tasks/{task_id}/reconcile-queue
```

Check event `state`, `task_id`, and board cards after ingest, after secretary
triage, and after user Go or Skip on inbox items.
