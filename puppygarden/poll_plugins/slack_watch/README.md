# slack_watch

Poll plugin that ingests inbound Slack messages (channels, DMs, MPIMs, and
thread replies) as `slack.message.received` task events. It polls Slack on a
fixed interval and emits one event per new message from anyone other than the
authenticated user.

This is the **pull-based** inbound path. Slack has no firehose endpoint, so this
plugin fans out: discover conversations → fetch new history per active
conversation → fetch new thread replies. See `puppygarden/docs/POLL_PLUGINS.md`
for the host contract.

## No persisted Slack token — direct MCP

This plugin does **not** read or persist any Slack token. It drives a Slack MCP
server directly via the generic `mcp` Python SDK (stdio transport), calling the
`slack_read_api_call` tool with `raw: true` to get full Slack Web API JSON.

## Singleton — runs on the secretary's host only

`config.yaml` sets `singleton: true` + `bound_role: secretary`, so this
plugin runs only on the host pinned to the secretary role (read fresh from
the roles store, cached ~60s). The pin is sticky/user-controlled; if that
host is down nobody runs the plugin until the user reassigns the secretary
role. This avoids duplicate polling across multiple hosts.

The MCP launch command lives in `config.yaml` under `mcp:`:

- **At Databricks**: `dbexec repo run mcp start-single slack` — `dbexec`
  resolves auth via dbcert at runtime as the invoking user. No token is ever
  written to disk. The MCP acts as *you* (your Slack identity), so it sees your
  personal DMs/MPIMs/channels including other people's messages to you.
- **Elsewhere**: swap in your own launch command (`npx`/`uvx`/binary) and add
  any env vars that server needs under `mcp.env` (e.g. `SLACK_BOT_TOKEN`).

`run.py` imports only the open `mcp` SDK — it contains zero Databricks-specific
or hardcoded launch strings; everything comes from `config.yaml`.

## Why polling (not push)

Slack push (Events API / Socket Mode / RTM) needs app config or scopes this
plugin does not have. The MCP's user-scoped auth can read the conversations
you're a member of via the Web API, so polling is the complete inbound
solution. Latency = the poll `interval_s` (no sub-second push).

## Files

- `run.py` — entry point executed by the host every `interval_s`.
- `config.yaml` — MCP launch config, types, limit, backfill, filters.
- `state.yaml` — written by `run.py` each tick; the watermark store.

## config.yaml

| key | default | meaning |
|---|---|---|
| `interval_s` | 180 | host poll cadence (3 min) |
| `mcp.command` | `dbexec` | how to launch the Slack MCP server |
| `mcp.args` | `["repo","run","mcp","start-single","slack"]` | launch args |
| `mcp.env` | (inherit) | optional env vars for non-dbcert auth |
| `types` | `public_channel,private_channel,im,mpim` | `conversations.list` types filter |
| `limit` | 200 | page size for list/history/replies |
| `backfill_s` | 180 | on first discovery, ingest the last N seconds (must be ≥ `interval_s`) |
| `ignore_bots` | false | skip messages carrying a `bot_id` |
| `max_age_s` | 1209600 | skip messages older than N seconds (2 weeks) |
| `all_events_channels` | `[]` | channels where ALL messages are emitted (not just mentions); names or IDs |
| `ignore_subtypes` | join/leave/topic/… | skip noisy non-human `subtype`s |
| `seen_bound` | 2000 | cap on the in-memory dedup set |

## state.yaml shape

```yaml
conversations:
  <channel_id>:
    name: <str>            # channel name (empty for IMs)
    kind: channel|im|mpim
    updated: <float>       # last observed Slack `updated` (seconds)
    watermark: <float>     # ts of last ingested message (seconds)
    partner: <user_id>     # other participant (IMs only)
threads:
  "<channel_id>:<thread_ts>":
    watermark: <float>     # ts of last ingested reply (seconds)
seen:                     # bounded list of "{channel}:{ts}" dedup keys
  - C02EPKPGB:1786850649.499709
```

## How a tick works

1. Spawn the MCP server (per `mcp:` config), initialize, `auth.test` → resolve
   `self_user_id` (to filter our own messages).
2. **Discovery**: `conversations.list` first page (newest first), diff IDs
   against `conversations`. New IDs are added with
   `watermark = now - backfill_s`. `backfill_s` must be ≥ `interval_s` so a
   brand-new DM created between ticks has its initiating message ingested.
3. **History**: for each conversation where `updated > watermark`, call
   `conversations.history(oldest=watermark)`. Dormant conversations
   (`updated <= watermark`) are skipped — zero API cost. Filter out self,
   messages older than `max_age_s`, bots (if `ignore_bots`), and noisy
   subtypes. Then:
   - **all-events channels** (IMs, MPIMs, and channels in `all_events_channels`):
     emit every remaining message; thread fan-out for roots with new replies.
   - **mentions_only channels** (all other channels): only emit messages
     containing `<@self_user_id>`. For mention messages that are part of a
     thread, fetch `conversations.replies` and emit all new replies in that
     thread. Non-mention messages advance the watermark but are not emitted.
   Advance `watermark` only on successful event POST (HTTP 200) — on failure,
   stop processing this conversation and retry next tick.
4. **Threads**: for a parent whose `latest_reply > thread_watermark`, call
   `conversations.replies(oldest=thread_watermark)` and emit new replies.
5. Save state. The MCP subprocess is torn down at end of tick.

All Slack calls go through `slack_read_api_call(endpoint=…, params=…, raw=true)`.

Dedup: Slack `ts` is unique per message; `{channel}:{ts}` keys are tracked in
`seen` (bounded) so boundary messages re-fetched across ticks are not
double-emitted. Watermarks are also advanced past the newest ts.

## Emitted events

- `slack.message.received` — one per new inbound message.
  - `source = poll_plugin:slack_watch`
  - `source_key = "<channel_id>:<ts>"` (dedup key)
  - `source_offset: 1`
  - `payload`: `channel_id`, `channel`, `kind`, `user`, `text`, `ts`, optional
    `thread_ts`, optional `partner`.

## Limitations

- **No push latency**: a new message is seen within `interval_s`, not instantly.
- **Per-tick MCP spawn**: each tick spawns a fresh `dbexec` subprocess (a couple
  seconds startup). Fine for a 3-min interval; would matter for sub-second polling.
- **First-page discovery only**: a full `conversations.list` pagination is not
  done each tick; only the first (newest) page. New conversations appear there,
  so they're detected within one tick. If `state.yaml` is deleted, only the
  first page is re-cached — run a manual full scan or accept the partial cache.
- **Membership-bound**: the MCP auth can only read conversations it's a member
  of. A user-scoped auth reads your DMs/channels; a bot-scoped auth reads only
  bot-joined ones.
- **Edits/deletes**: edits re-emit the same `ts` (dedup suppresses); deletes
  are not surfaced.
- **Event POST failure**: if the Omnigent server returns non-200, the
  watermark does not advance past that message. Processing stops for the
  conversation and resumes next tick, retrying the failed message.

## Editing this plugin

**Agents editing this plugin MUST read this README first.** The watermark
contract is subtle and easy to break:

- **Never regress a `watermark`** to re-ingest history — it double-emits.
  Delete `state.yaml` only to reset everything.
- **`backfill_s` must stay ≥ `interval_s`** or brand-new DMs' initiating
  messages fall in the gap and are missed.
- **Thread fan-out keys** are `"<channel_id>:<thread_ts>"` and must match the
  `threads` map exactly; renaming the key scheme orphans thread watermarks.
- **`seen` is dedup, not state of record** — watermarks are. Do not rely on
  `seen` for correctness across restarts (it's bounded and in-memory).
- **Watermark advances only on successful POST** — `emit_message` returns
  `False` on HTTP failure; the loop breaks and the watermark stays at the
  last successfully emitted message. Do not change this without ensuring
  events are not silently lost.
- **`max_age_s` drops stale messages** — messages older than `max_age_s`
  are skipped and the watermark advances past them. They are not retried.
- The filter order in `run.py` (max_age → self → bots → subtypes → mentions)
  determines what's emitted; changing it changes the event stream
  retroactively for new messages.
- **Do not hardcode a Slack token or launch command in `run.py`.** All transport
  config lives in `config.yaml` under `mcp:` so the code stays org-agnostic.
