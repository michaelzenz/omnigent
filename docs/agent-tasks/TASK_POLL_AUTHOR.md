# Poll plugin author manual

You create and maintain **poll plugins** under the host plugin directory.
You do **not** change Omnigent host or server code.

Read **[POLL_PLUGINS.md](POLL_PLUGINS.md)** for the full contract.

## Your job

Given user instructions (from chat or a future UI form):

1. Understand what external signal to watch (GitHub PR, CI, etc.).
2. Create or update a plugin folder with a working **`run.py`**.
3. Add plugin-owned state files (`watches.json`, `state.json`, …) as needed.
4. Ensure transitions POST **`/v1/task-events`** with routing-friendly `summary` text.

## Workflow

### New plugin

1. Pick a short folder name: `github_pr`, `buildkite_main`, …
2. Create `{poll_plugins_dir}/<name>/run.py` (see skeleton in POLL_PLUGINS.md).
3. Create `{poll_plugins_dir}/<name>/config.yaml` with `interval_s`.
4. Add `watches.json` if the user specified explicit targets.
5. Document behavior in a one-line comment at the top of `run.py`.

Default plugin directory:

- `$OMNIGENT_DATA_DIR/poll_plugins/` or `~/.omnigent/poll_plugins/`

Plugins run automatically when the host daemon is up. An empty directory is a no-op.

### Update existing plugin

1. Read current `run.py` and state files in that folder.
2. Apply the user's change (new watch, new event type, extra fields).
3. Preserve idempotent diff logic — do not re-emit events for unchanged state.
4. Bump `source_offset` or use distinct `event_type` when emitting new transition kinds.

### User instruction examples

| User says | You do |
|-----------|--------|
| "Watch PR 456 in org/repo until it merges; unblocks my PR 123" | Edit `github_pr/watches.json` with `context.blocked_pr` and `context.task_id`; ensure `run.py` emits `github.pr.merged` with `task_id` |
| "Also poll PRs where I'm requested reviewer" | Extend `run.py` auto_discover or `watches.json` |
| "Stop watching 456" | Remove from `watches.json` or set `"active": false` |

## Task events

Always use ingress dedup fields:

- `source`: `poll_plugin:<plugin_name>`
- `source_key`: stable id, e.g. `org/repo#456`
- `source_offset`: increment per transition type (or use head SHA hash)

Put routing tokens in **`summary`**: `repo:…`, `pr:…`, `unblocks:pr:…`.

When the manager supplies a managed task id, set `context.task_id` on the watch
so follow-up events POST with `task_id` and skip distributor scoring.

## Testing

Run manually (same as host):

```bash
export OMNIGENT_SERVER_URL=http://127.0.0.1:8123
export OMNIGENT_HOST_ID=<host_id>
export OMNIGENT_PLUGIN_DIR=~/.omnigent/poll_plugins/github_pr
export OMNIGENT_PLUGIN_NAME=github_pr
export OMNIGENT_DATA_DIR=~/.omnigent
python3 run.py
```

Check server: `GET /v1/task-events` for new events.

## Do not

- Edit `omnigent/host/polling/` or server routes.
- Restart the host — plugin changes apply on the next poll tick.
- Put secrets in plugin files.
- Create multiple entry points — only **`run.py`** is executed.
