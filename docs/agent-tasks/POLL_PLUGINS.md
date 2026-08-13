# Poll plugins

Host-side scripts that watch external signals and emit task events.
Each plugin is one folder; the host only executes **`run.py`**.

## Layout

```
~/.omnigent/poll_plugins/          # or $OMNIGENT_DATA_DIR/poll_plugins/
  <plugin_name>/
    run.py                         # required — sole entry point
    config.yaml                    # required — poll interval for this plugin
    *.json / *.yaml                # optional — plugin-owned state (agent-generated)
```

Each plugin folder must include **`config.yaml`** with at least:

```yaml
interval_s: 60
description: <a one line description what this plugin does>
```

Optional per-plugin overrides:

```yaml
interval_s: 120
timeout_s: 90
```

Host-wide defaults (when a plugin omits a field) live in `~/.omnigent/config.yaml`:

```yaml
host:
  polling:
    poll_plugins:
      default_interval_s: 60
      default_timeout_s: 120
      tick_s: 5
```

`tick_s` is how often the host checks which plugins are due; each plugin runs
when its own `interval_s` has elapsed.

Rules:

- **One folder per plugin** — stable name (`github_pr`, `slack_watch`, …).
- **Only `run.py` is executed** — host runs `python3 <plugin_dir>/run.py`.
- **All other files are plugin-private** — watches, cursors, snapshots; agents design the plugin schema or other metadata file as they want.
- Do not edit Omnigent host code to add behavior — add or update a plugin folder.

## Host contract

The host `ScriptPollPluginsPoller` invokes each plugin on a schedule:

```bash
python3 ~/.omnigent/poll_plugins/<plugin_name>/run.py
```

Environment (set by host):

| Variable | Meaning |
|----------|---------|
| `OMNIGENT_SERVER_URL` | Base URL, e.g. `http://127.0.0.1:8123` |
| `OMNIGENT_HOST_ID` | This machine's host id |
| `OMNIGENT_PLUGIN_DIR` | Absolute path to this plugin's folder |
| `OMNIGENT_PLUGIN_NAME` | Folder name (`github_pr`) |
| `OMNIGENT_DATA_DIR` | Data root (`~/.omnigent` or override) |

`run.py` should:

1. Be **idempotent** — safe to run every tick.
2. **Read/write state only under `OMNIGENT_PLUGIN_DIR`** (or subpaths).
3. **Exit 0** on success (including “nothing to do”).
4. **Exit non-zero** on failure; host logs stderr and continues other plugins.
5. **Post task events** when something meaningful changes (see below).

`run.py` must finish within the host timeout (default 120s).

## Emitting task events

When status changes, POST to the existing ingress API:

```
POST {OMNIGENT_SERVER_URL}/v1/task-events
Header: X-Omnigent-Host-Id: {OMNIGENT_HOST_ID}
```

Example body:

```json
{
  "event_type": "github.pr.merged",
  "title": "PR #456 merged in org/repo",
  "summary": "repo:org/repo pr:456 unblocks:pr:123",
  "source": "poll_plugin:github_pr",
  "source_key": "org/repo#456",
  "source_offset": 1,
  "tags": [
    {"tag_type": "repo", "tag": "org/repo"},
    {"tag_type": "pr", "tag": "456"}
  ],
  "payload": {
    "repo": "org/repo",
    "pr_number": 456,
    "blocked_pr": 123
  },
  "task_id": "<managed-task-id>"
}
```

When a watch is tied to a specific managed task, include `task_id` on the
ingress body (or `context.task_id` in `watches.json` for the `github_pr`
plugin). The ingress scorer routes directly to that task and skips scoring.

Dedup: same `source` + `source_key` + `source_offset` + `event_type` → server returns existing event.

### Suggested `event_type` prefixes

- `github.pr.merged`
- `github.pr.checks_passed`
- `github.pr.checks_failed`

Plugins may define other prefixes; document them in a comment at the top of `run.py`.

## State files (plugin-owned)

The server does **not** store poll cursors or watch lists. Plugins keep their own files, e.g.:

| File | Typical use |
|------|-------------|
| `watches.json` | What to poll (agent/manager edits) |
| `state.json` | Last-seen snapshot per key (script maintains) |

Example `watches.json`:

```json
{
  "auto_discover": ["authored", "review_requested"],
  "explicit": [
    {
      "repo": "org/repo",
      "pr": 456,
      "context": {"blocked_pr": 123, "task_id": "<managed-task-id>"}
    }
  ]
}
```

Example `state.json`:

```json
{
  "org/repo#456": {
    "state": "OPEN",
    "checks": "FAILURE",
    "head_sha": "abc123"
  }
}
```

Agents may invent other filenames; only `run.py` is invoked by the host.

## Minimal `run.py` skeleton

See `examples/poll_plugins/github_pr/run.py` in the repository.

## Example: blocked PR scenario

1. `github_pr` plugin auto-discovers your open PR #123.
2. CI fails → `run.py` posts `github.pr.checks_failed` with `task_id`
3. Ingress fast-paths to that task → manager suggest "investigate the CI failure" as taskItem.

## Do not

- Rename `run.py` or expect the host to run other files.
- Edit `omnigent/host/` for plugin behavior — use this folder instead.
