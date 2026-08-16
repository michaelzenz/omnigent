# Poll plugins

Host-side scripts that watch external signals and emit task events.
Each plugin is one folder; the host only executes **`run.py`**.

## Layout

```
~/.omnigent/poll_plugins/          # or $OMNIGENT_DATA_DIR/poll_plugins/
  <plugin_name>/
    run.py                         # required — sole entry point
    README.md                      # required — plugin docs (see below)
    config.yaml                    # required — poll interval for this plugin
    *.json / *.yaml                # optional — plugin-owned state (agent-generated)
```

### Where the host scans for plugins

By default the host scans `<data_dir>/poll_plugins` (`~/.omnigent/poll_plugins`
or `$OMNIGENT_DATA_DIR/poll_plugins`). A from-source checkout can point the host
at a version-controlled plugins directory instead, so edits take effect on the
next tick with no copy/sync — set `host.polling.poll_plugins.root` in
`~/.omnigent/config.yaml`:

```yaml
host:
  polling:
    poll_plugins:
      root: /path/to/omnigent/puppygarden/poll_plugins
```

The bundled, usable plugins live in the repo at `puppygarden/poll_plugins/`. To run
them from a cloned checkout (temporary approach — until the host supports a
proper plugin install/sync), point the host at that directory by setting
`host.polling.poll_plugins.root` in `~/.omnigent/config.yaml`:

```yaml
host:
  polling:
    poll_plugins:
      root: /path/to/your/omnigent/clone/puppygarden/poll_plugins
```

The host scans that directory directly, so edits to repo plugins take effect
on the next tick with no copy/sync.

Each plugin folder must include **`config.yaml`** with at least:

```yaml
interval_s: 60
description: <a one line description what this plugin does>
singleton: true/false      # required — every plugin must declare this explicitly
bound_role: secretary      # required when singleton: true (inert when singleton: false)
```

A config that omits `singleton`, or sets `singleton: true` without `bound_role`,
is **rejected at load** — the host logs a warning and skips the plugin. 

Optional per-plugin overrides:

```yaml
interval_s: 120
timeout_s: 90
```

### Singleton plugins (run on exactly one host)

```yaml
singleton: true
bound_role: secretary
```

- `singleton` is **required** (true or false). `singleton: true` requires
  `bound_role` (a role key, e.g. `secretary`); without it the plugin is
  rejected at load. `singleton: false` runs on every host.
- The host reads the bound role's pinned `host_id` from the server
  (`GET /v1/agent-tasks/roles/{bound_role}/profile`) and runs the plugin only
  when that `host_id` equals its own `OMNIGENT_HOST_ID`. The read is cached
  ~60s.
- On any fetch failure (server unreachable, HTTP error), the host skips the
  plugin that tick (safe — no duplicate runs across hosts).

> **Note:** host-side singleton gating is a temporary solution. The durable
> answer is to support creating custom plugins on the server (lifecycle,
> scheduling, and state live server-side, independent of which host is
> connected). We should design that server-side plugin model later and
> retire this host-side gating.

### `--healthcheck`

Plugins SHOULD support `python3 run.py --healthcheck`:

- Exit `0` + JSON on stdout `{"ok": true, "detail": "..."}` when the plugin
  can reach its backing service (e.g. spawn the MCP and call `auth.test`).
- Exit non-zero + `{"ok": false, "detail": "..."}` otherwise.
- Cheap, side-effect-free.

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
- **`README.md` is required** — the host skips any plugin folder missing it.
  **An agent updating a poll plugin
  MUST read that plugin's `README.md` first** — it is the source of truth for
  the plugin's state shape and contracts, and editing without it risks
  duplicate or missed events.
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

See `puppygarden/poll_plugins/github_pr/run.py` in the repository.

## Example: blocked PR scenario

1. `github_pr` plugin auto-discovers your open PR #123.
2. CI fails → `run.py` posts `github.pr.checks_failed` with `task_id`
3. Ingress fast-paths to that task → manager suggest "investigate the CI failure" as taskItem.

## Do not

- Rename `run.py` or expect the host to run other files.
- Edit `omnigent/host/` for plugin behavior — use this folder instead.
