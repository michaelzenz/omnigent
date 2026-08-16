# reminder

Timer plugin that fires a one-shot `timer.reminder` task event at a wall-clock
`fire_at`. To make it recurring, `run.py` re-arms by writing a new future
`fire_at` to `config.yaml` before exiting (see the commented block in `run.py`).

## Files

- `run.py` — entry point executed once when `fire_at` is due.
- `config.yaml` — `fire_at` (unix seconds) and optional `title`.

## How firing works

The host ticks every `timer_plugins.tick_s`. When `now >= fire_at` and the
plugin has not already fired this `fire_at` (tracked in `state.yaml`), it runs
`run.py` once, then writes `state.yaml` so the same `fire_at` is never re-fired —
even across restarts. To re-arm, `run.py` writes a new future `fire_at` to
`config.yaml`.

## state.yaml

```yaml
fired_at: <unix>
```

Written by the host after each fire. Delete it to re-fire the current `fire_at`.

## Emitted events

- `timer.reminder` — `source = timer_plugin:reminder`, `source_key = "<fire_at>"`,
  `source_offset: 1`. On failure the host emits `timer.fire_failed` instead.

## Editing this plugin

**Agents editing this plugin MUST read this README first.** The re-arm contract
is: `run.py` may rewrite `config.yaml`'s `fire_at`, and the host owns
`state.yaml`. Do not write `state.yaml` from `run.py` — the host treats it as its
own last-fired marker; writing it from the plugin can suppress or duplicate
fires.
