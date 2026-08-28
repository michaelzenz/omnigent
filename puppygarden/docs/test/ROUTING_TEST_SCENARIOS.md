# Routing test scenarios

Manual scenarios for exercising task-event ingress, ingress auto-route,
broker triage, and manager reconciliation. Run against a local server
(`http://127.0.0.1:6767`) with host connected.

Use a **fresh database** (no active tasks, no open packages) unless a
scenario says otherwise. Adjust `repo`, agent ids, and host headers to match your
environment.

---

## Scenario 1 — CI failure on USM schema parse + Slack follow-up on fix PR (empty DB)

**Story.** PR [#2248509](https://github.com/databricks-eng/universe/pull/2248509)
in `databricks-eng/universe` fails CI with a USM schema-parse error. Later,
Veeresh posts in Slack channel `#usm-help` (C04LQT17RKM) that the fix PR is
ready and waiting for merge approval. There are no managed tasks yet.

**Goal.** _To be defined._

### Preconditions

1. **Empty task state** — no active tasks, no open pending packages, no
   stalled events in `awaiting_grouping` (wipe DB or use a clean server).
2. Server and host running (`uv run omnigent server`, `uv run omnigent host`).
3. Task broker session live (`POST /v1/agent-tasks/roles/broker/session`). This
   also launches the session runner when a host is connected (no separate
   `POST /v1/hosts/{hostId}/runners` step).

### Events

Post in order (replace `HOST_ID` with your registered host id). Wait for broker
triage after event 1 before posting event 2, or post both and let the broker
catch up in one batch.

**Event 1 — CI failure on PR #2248509**

```http
POST /v1/task-events
X-Omnigent-Host-Id: HOST_ID

{
  "event_type": "github.pr.checks_failed",
  "title": "PR #2248509 checks failed",
  "summary": "repo:databricks-eng/universe pr:2248509 Failed to parse USM generated schema json: Please report this to #usm-help channel and add the label: bypass-usm-ci-checks to bypass the USM CI checks.",
  "source": "github",
  "source_key": "databricks-eng/universe#2248509",
  "source_offset": "1",
  "tags": [
    {"tag_type": "repo", "tag": "databricks-eng/universe"},
    {"tag_type": "pr", "tag": "2248509"}
  ],
  "payload": {
    "repo": "databricks-eng/universe",
    "pr_number": 2248509,
    "failure": "Failed to parse USM generated schema json: Please report this to #usm-help channel and add the label: bypass-usm-ci-checks to bypass the USM CI checks."
  }
}
```

**Event 2 — Slack: Veeresh says fix PR is ready, waiting for approval**

```http
POST /v1/task-events
X-Omnigent-Host-Id: HOST_ID

{
  "event_type": "slack.message",
  "title": "Veeresh: fix PR is ready, waiting for approval",
  "summary": "repo:databricks-eng/universe slack:C04LQT17RKM Veeresh says the fix PR is ready and waiting for merge approval",
  "source": "slack",
  "source_key": "slack:C04LQT17RKM:1784710613514779",
  "source_offset": "2",
  "tags": [
    {"tag_type": "repo", "tag": "databricks-eng/universe"},
    {"tag_type": "slack_channel", "tag": "C04LQT17RKM"}
  ],
  "payload": {
    "channel": "C04LQT17RKM",
    "channel_name": "usm-help",
    "author": "veeresh",
    "text": "fix PR is ready, waiting for approval",
    "thread_ts": "1784650136.994509",
    "message_ts": "1784710613514779"
  }
}
```

### Pass criteria

_To be defined._

### Failure modes to watch

_To be defined._

### Verify

```http
GET /v1/task-events/ambiguous-inbox
GET /v1/agent-tasks?state=pending
GET /v1/agent-tasks/{task_id}/reconcile-queue
```
