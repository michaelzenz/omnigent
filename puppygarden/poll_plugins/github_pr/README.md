# github_pr

Poll plugin that watches GitHub PR status and emits task events on transitions
(merged / checks passed / checks failed).

## Files

- `run.py` — entry point executed by the host every `interval_s`.
- `config.yaml` — poll interval/timeout overrides.
- `watches.json` — what to watch.
- `state.json` — written by `run.py` each tick; the last-seen snapshot per PR
  (`mergedAt`, `checks`, `headRefOid`). Delete to re-emit current state as new events.

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
| `github.pr.merged` | 1 | `mergedAt` newly present |
| `github.pr.checks_failed` | 2 | checks rollup becomes `FAILURE` |
| `github.pr.checks_passed` | 3 | checks rollup becomes `SUCCESS` |

`source = poll_plugin:github_pr`, `source_key = "<repo>#<pr>"`.

## Requirements

- `gh` CLI authenticated in the host environment.

## Editing this plugin

**Agents editing this plugin MUST read this README first.** The state shape
(`state.json` keyed by `repo#pr`) and the transition detection logic in `run.py`
are coupled — changing one without the other causes duplicate or missed events.
