# Timer plugins

Host-side scripts that fire once at a declared wall-clock time and emit task
events (or run arbitrary side effects). Each plugin is one folder; the host
only executes **`run.py`**.

Timer plugins are the one-shot counterpart to [poll plugins](POLL_PLUGINS.md):
poll plugins fire on a fixed interval; timer plugins fire when their
`fire_at` is reached.

## Layout

```
~/.omnigent/timer_plugins/         # or $OMNIGENT_DATA_DIR/timer_plugins/
  <plugin_name>/
    run.py                         # required — sole entry point
    README.md                      # required — plugin docs (see below)
    config.yaml                    # required — must contain fire_at
    state.yaml                     # host-managed — tracks last fired_at
    *.json / *.yaml                # optional — plugin-owned state (agent-generated)
```

### Where the host scans for plugins

By default the host scans `<data_dir>/timer_plugins` (`~/.omnigent/timer_plugins`
or `$OMNIGENT_DATA_DIR/timer_plugins`). A from-source checkout can point the host
at a version-controlled plugins directory instead — set
`host.polling.timer_plugins.root` in `~/.omnigent/config.yaml`:

```yaml
host:
  polling:
    timer_plugins:
      root: /path/to/omnigent/puppygarden/timer_plugins
```

The bundled, usable plugins live in the repo at `puppygarden/timer_plugins/`. To
run them from a cloned checkout (temporary approach — until the host supports a
proper plugin install/sync), point the host at that directory by setting
`host.polling.timer_plugins.root` in `~/.omnigent/config.yaml`:

```yaml
host:
  polling:
    timer_plugins:
      root: /path/to/your/omnigent/clone/puppygarden/timer_plugins
```

The host scans that directory directly, so edits to repo plugins take effect
on the next tick with no copy/sync.

Each plugin folder must include **`config.yaml`** with at least:

```yaml
fire_at: 1700000000                # required — unix timestamp (seconds)
singleton: true/false               # required — every plugin must declare this explicitly
bound_role: secretary               # required when singleton: true (inert when singleton: false)
```

A config that omits `singleton`, or sets `singleton: true` without `bound_role`,
is **rejected at load** — the host logs a warning and skips the timer (it does
not fire). Surfacing that failure to the user is a later concern (server-side
plugin monitoring).

Optional per-plugin overrides:

```yaml
fire_at: 1700000000
timeout_s: 90                      # subprocess timeout override
```

### Singleton timers (fire on exactly one host)

A timer that should fire on exactly one host (not on every host that has it)
opts into singleton with the same semantics as poll plugins:

```yaml
singleton: true
bound_role: secretary
```

- `singleton` is **required** (true or false). `singleton: true` requires
  `bound_role` (a role key, e.g. `secretary`); without it the timer is rejected
  at load. `singleton: false` fires on every host (`bound_role` is inert and
  optional).
- The host reads the bound role's pinned `host_id` and fires the timer only
  when that equals its own `OMNIGENT_HOST_ID` (cached ~60s).
- The pin is **sticky/user-controlled** — never auto-reassigned. If the
  pinned host is down, the timer does not fire until the user reassigns the
  role.
- A skipped singleton timer is **not** marked fired, so the winning host
  (once the role is reassigned to it) can still fire the due `fire_at`.

### `--healthcheck`

Timer plugins SHOULD support `python3 run.py --healthcheck` with the same
contract as poll plugins (exit 0 + `{"ok": true, "detail": "..."}` on
success). It is for later server-side monitoring, not scheduling.

Host-wide defaults (when a plugin omits a field) live in `~/.omnigent/config.yaml`:

```yaml
host:
  polling:
    timer_plugins:
      default_timeout_s: 120
      tick_s: 30
```

`tick_s` is how often the host checks which timers are due.

Rules:

- **One folder per plugin** — stable name (`reminder`, `daily_report`, …).
- **Only `run.py` is executed** — host runs `python3 <plugin_dir>/run.py`.
- **`README.md` is required** — the host skips any plugin folder missing it.
  Every plugin MUST ship a `README.md` describing its purpose, state shape,
  emitted event types, and the re-arm contract. **An agent updating a timer
  plugin MUST read that plugin's `README.md` first** — it is the source of
  truth for the fire/re-arm contract and the `state.yaml` ownership rule.
- **`config.yaml` is required** and must contain `fire_at` — a unix timestamp
  (seconds) at which the timer fires.
- **`state.yaml` is host-managed** — the host writes `fired_at` after each
  invocation so the same `fire_at` is never re-fired, even across restarts.
- Do not edit Omnigent host code to add behavior — add or update a plugin folder.

## Host contract

The host `ScriptTimerPluginsPoller` scans `timer_plugins/` every `tick_s`
seconds. For each plugin whose `fire_at` has been reached and whose
`state.yaml` `fired_at` is older than `fire_at`, the host runs:

```bash
python3 ~/.omnigent/timer_plugins/<plugin_name>/run.py
```

Environment (set by host):

| Variable | Meaning |
|----------|---------|
| `OMNIGENT_SERVER_URL` | Base URL, e.g. `http://127.0.0.1:8123` |
| `OMNIGENT_HOST_ID` | This machine's host id |
| `OMNIGENT_PLUGIN_DIR` | Absolute path to this plugin's folder |
| `OMNIGENT_PLUGIN_NAME` | Folder name (`reminder`) |
| `OMNIGENT_DATA_DIR` | Data root (`~/.omnigent` or override) |

`run.py` should:

1. Be **idempotent** — safe to run if the host re-invokes it.
2. **Read/write state only under `OMNIGENT_PLUGIN_DIR`** (or subpaths).
3. **Exit 0** on success (including "nothing to do").
4. **Exit non-zero** on failure; host logs stderr and **does not retry** —
   `fired_at` is written regardless, so a failed timer will not fire again
   until you set a new `fire_at`.
5. **Do whatever it wants** — emit a task event (see below), call an API,
   touch a file, etc. The host imposes no output contract beyond exit code.

`run.py` must finish within the host timeout (default 120s).

## Fire semantics

```
read config.yaml → fire_at
read state.yaml  → fired_at (default 0)
if fire_at is None:        skip (disabled / consumed)
if now < fire_at:          skip (not due yet)
if fired_at >= fire_at:    skip (already fired for this fire_at)
# due and not yet fired:
  run python3 <plugin_dir>/run.py (with env, timeout)
  write state.yaml → fired_at = now   # regardless of exit code — no retry
```

- **One-shot**: leave `fire_at` alone. After firing, `fired_at >= fire_at`
  so it never re-fires. To re-arm, set a new `fire_at`.
- **Recurring**: `run.py` writes a new future `fire_at` to `config.yaml`
  before exiting. Next tick, `fired_at < new_fire_at` → fires again when due.
- **Disable**: set `fire_at: null` (or delete the field) → skipped.

## Emitting task events

When the timer should surface to a task manager, POST to the ingress API:

```
POST {OMNIGENT_SERVER_URL}/v1/task-events
Header: X-Omnigent-Host-Id: {OMNIGENT_HOST_ID}
```

Example body:

```json
{
  "event_type": "timer.reminder",
  "title": "Daily standup reminder",
  "summary": "timer:reminder fire_at:1700000000",
  "source": "timer_plugin:reminder",
  "source_key": "1700000000",
  "source_offset": 1,
  "payload": {"plugin": "reminder", "fire_at": 1700000000},
  "task_id": "<managed-task-id>"
}
```

When tied to a specific managed task, include `task_id` on the ingress body —
the ingress scorer routes directly to that task and skips scoring.

Dedup: same `source` + `source_key` + `source_offset` + `event_type` → server
returns existing event.

### Suggested `event_type` prefixes

- `timer.reminder`
- `timer.deadline`
- `timer.heartbeat`

The host itself emits `timer.fire_failed` when a plugin's `run.py` exits
non-zero, times out, or fails to start. The event body includes `reason`
(`exit_nonzero`, `timeout`, or `start_failed`), `exit_code` (when available),
and a truncated `detail` (stderr snippet).

Plugins may define other prefixes; document them in a comment at the top of
`run.py`.

## State files (plugin-owned)

The host does **not** store poll cursors. Plugins keep their own files (under
`OMNIGENT_PLUGIN_DIR`), e.g.:

| File | Typical use |
|------|-------------|
| `config.yaml` | `fire_at` (agent/host edits) — host reads, `run.py` may rewrite |
| `state.yaml` | `fired_at` (host writes after each fire) |
| `notes.json` | Plugin-private notes (agent-generated) |

Only `run.py` is invoked by the host; `state.yaml` is written by the host.

## Minimal `run.py` skeleton

See `puppygarden/timer_plugins/reminder/run.py` in the repository.

## Example: recurring reminder

1. `reminder/config.yaml` starts with `fire_at: <tomorrow 9am>`.
2. Host fires `run.py` at that time → posts `timer.reminder` task event.
3. `run.py` re-arms: writes `fire_at: <next day 9am>` to `config.yaml`.
4. Host fires again the next day — recurring.

## Do not

- Rename `run.py` or expect the host to run other files.
- Edit `omnigent/host/` for plugin behavior — use this folder instead.
- Expect retry on failure — `fired_at` is written regardless. To retry, set
  a new `fire_at`.
