# session_watcher

Poll plugin that discovers external coding-agent session transcripts (Codex,
Cursor) on disk and reports new sessions and transcript updates (including
rewinds) to the server.

## Files

- `run.py` — entry point executed by the host every `interval_s`.
- `config.yaml` — scan dirs, snippet/recency/sink tuning.
- `state.yaml` — written by `run.py` each tick.

## config.yaml

- `scan_dirs` — directories to `rglob` for transcript files.
- `snippet_lines` — trailing lines sent in the discovery `transcript_snippet`.
- `recency_window_s` — only sessions modified within this window are considered.
- `sink_time_s` — only sessions idle for at least this long are reported, so
  mid-turn sessions are not triaged.

## state.yaml shape

```yaml
sessions:
  <session_hint>:
    last_history_hash: <16-char sha prefix>
    history_length: <int>
    last_modified_at: <unix mtime>
rejected:
  - hint: <session_hint>
    rejected_at: <unix>
```

`session_hint` is `sha256(path)[:16]`. History is a chained hash
`h[i] = sha256(h[i-1] || message[i])` so rewinds are detected by divergence
point, not just length.

## Emitted events

- `external.session.discovered` — new session; posted to `/v1/task-events`.
- Updates go to `/v1/session-watcher/update` with `transcript_delta` and optional
  `rewind_at`. The server's `track` response can reject a session (added to
  `rejected`, never re-reported).

## Editing this plugin

**Agents editing this plugin MUST read this README first.** The chained-hash
history and divergence logic in `run.py` are coupled to the `state.yaml` shape;
changing the hash scheme or state fields without migrating `state.yaml` breaks
rewind detection. The `rejected` list is append-only — do not prune without a
policy.
