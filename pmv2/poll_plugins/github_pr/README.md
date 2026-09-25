# github_pr

Poll plugin that watches GitHub PR status and comments, emitting task events
on transitions (merged / checks failed / new comments / replies).

## Files

- `run.py` — entry point executed by the host every `interval_s`.
- `config.yaml` — poll interval/timeout overrides.
- `watches.json` — what to watch.
- `state.json` — written by `run.py` each tick; the last-seen snapshot per PR
  (`mergedAt`, `checks`, `headRefOid`, `last_comment_at`, `seen_comment_ids`).
  Delete to re-emit current state as new events.

## watches.json shape

```json
{
  "auto_discover": ["authored", "review_requested"],
  "explicit": [{"repo": "owner/name", "pr": 1234, "context": {}}]
}
```

- `auto_discover` — `gh search prs` queries run each tick: `author:@me` and/or
  `review-requested:@me`.
- `explicit` — fixed list of `{repo, pr, context}`. `context` is echoed into
  the event payload verbatim.

Events never name a task. To route a PR's events to a specific managed task,
subscribe the task server-side to (`poll_plugin:github_pr`, `<repo>#<pr>`) via
`POST /v1/agent-tasks/{id}/event-subscriptions`.

## Emitted events

| event_type | source_offset | trigger |
|---|---|---|
| `github.pr.merged` | `"merged"` | `mergedAt` newly present |
| `github.pr.checks_failed` | `"checks_failed"` | checks rollup becomes `FAILURE` |
| `github.pr.comment.new` | `"comment:<id>"` | new top-level issue comment or standalone inline comment |
| `github.pr.comment.reply_to_me` | `"comment:<id>"` | inline reply to a comment authored by me |
| `github.pr.comment.reply_to_other` | `"comment:<id>"` | inline reply to a comment authored by someone else |

`source = poll_plugin:github_pr`, `source_key = "<repo>#<pr>"`.

`source_offset` is a dedup cursor: `"merged"` and `"checks_failed"` are stable
per PR, while `"comment:<id>"` is unique per comment — the server dedup keys
on `(source, source_key, source_offset, event_type)`.

## Retry behavior

If the server POST returns non-200, the event is **not** marked as fired.
State is not updated for that transition, so it re-detects and retries on the
next tick. `state.json` is always written at the end of each tick (even if
some PRs failed), so successful PRs are not lost.

## GC on merge

When a PR merges and the `github.pr.merged` event is successfully posted, the
PR's entry is removed from `state.json`. Merged PRs also drop off auto-discover
(`--state=open`), so they're never polled again.

## Requirements

- `gh` CLI authenticated in the host environment.

## Editing this plugin

**Agents editing this plugin MUST read this README first.** The state shape
(`state.json` keyed by `repo#pr`, with `last_comment_at` and `seen_comment_ids`
for comment dedup) and the transition detection logic in `run.py` are coupled
— changing one without the other causes duplicate or missed events.
