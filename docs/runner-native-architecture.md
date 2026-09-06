# Runner Architecture: Native Harnesses, the Bridge, and the app.py / native/ Split

## Status

Current as of the machinery consolidation (stages 0–2). This document explains
the component landscape of the runner, how the pieces fit together, and the
structural rules that keep the fork mergeable with upstream.

## The two planes

Omnigent splits into a control plane and a data plane:

- **Omnigent server** (control plane) owns the source of truth: session rows,
  labels, model catalog, permissions, auth. It never touches tmux or local
  processes.
- **Runner** (data plane) is the per-host agent process — `create_runner_app`
  in `omnigent/runner/app.py`. It owns everything process-side: tmux panes,
  CLI subprocesses, bridge directories, transcript forwarders.

They communicate over the WebSocket tunnel (`runner/transports/ws_tunnel`)
plus plain httpx calls. Upstream's `runner/routing.py` is the server-side
half: given a conversation, find the hosting runner via the tunnel registry
and return an HTTP client to it.

```
┌─────────────────────────┐          ┌───────────────────────────────┐
│  Omnigent SERVER         │  ws      │  RUNNER (one per host)        │
│  (control plane)         │◄─tunnel─►│  omnigent/runner/app.py       │
│  - sessions DB           │  +httpx  │  - owns tmux + CLI processes  │
│  - labels, model catalog │          │  - bridges events/files       │
│  - auth, permissions     │          │  - FastAPI (create_runner_app)│
└─────────────────────────┘          └───────────────────────────────┘
        ▲                                     ▲
        │ web UI / API                        │ spawns & supervises
        ▼                                     ▼
   human user                     native CLIs in tmux panes
                                  (Claude Code, Codex CLI, Pi, Cursor, …)
```

## Two senses of "harness"

This distinction causes most of the confusion, so it comes first:

- **Executor harness** — how Omnigent runs an agent loop. `claude-sdk` runs
  the Claude Agent SDK in-process; `onih-pi` runs Pi's RPC loop. No tmux, no
  bridge dir.
- **Native harness** — the vendor's own CLI running in a tmux pane:
  `claude-native`, `codex-native`, `cursor-native`, `pi-native`,
  `kiro-native`, `goose-native`, `hermes-native`, `antigravity-native`,
  `opencode-native`, `qwen-native`, `kimi-native`.

Everything in `runner/native/` and every `_launch_<key>` / `_auto_create_<key>_terminal`
symbol concerns **native harnesses only**.

## The runner's components

| Component | What it is | Home |
|---|---|---|
| Session | Conversation record. The server owns the row; the runner keeps runtime state (event queues, harness name, locks) keyed by `session_id` | server DB + dicts in `app.py` |
| Terminal / pane | The tmux pane running the vendor CLI. Tracked as a `SessionResourceView` in the terminal registry | tmux + `resource_registry.py` |
| Bridge dir | On-disk coordination point between runner and CLI: `~/.omnigent/<key>-native/<hash>/`. Holds `state.json` (app-server socket, thread id), `bridge.json` (relay token), hook configs. **`clear_bridge_state` wipes this at every fresh launch** | `<key>_native_bridge.py` |
| App-server (codex) | Codex's JSON-RPC sidecar (`ws://127.0.0.1:…`). Settings changes, thread discovery, and interrupts are RPCs against it | `codex_native_app_server.py` |
| Forwarder | Tails the CLI's own transcript (codex rollouts, claude transcript) and posts new items to the server. Cancelled and awaited on terminal re-create | `<key>_native_forwarder.py` |
| Tool relay | When the CLI wants an Omnigent tool (`sys_session_send`, `sys_os_read`, …) it POSTs to a local relay HTTP server → `ProxyMcpManager` → server `/mcp`. Its advertised tool list is built by `build_native_relay_tool_schemas` | `tool_dispatch.py`, `proxy_mcp_manager.py` |
| Inject | How the runner talks *into* the CLI: `inject_slash_command`, `inject_interrupt` write via the bridge/tmux | `<key>_native_bridge.py` |
| Interrupt / stop control | Unified per-harness interrupt and stop routing | `native/interrupt.py` |

### Per-harness vendor modules

Every native CLI has a module family following the same shape:

```
omnigent/codex_native.py              # CLI/resume launch, agent-spec materialization
omnigent/codex_native_bridge.py       # bridge dir, state.json, inject, clear
omnigent/codex_native_forwarder.py    # transcript tail → server items
omnigent/codex_native_app_server.py   # JSON-RPC sidecar (codex only)
```

These are upstream modules; the fork consumes them.

## Launch flow (fork: eager, at create)

The fork launches the native terminal **synchronously during session create**,
whereas upstream defers terminal creation to first attach (with a turn-time
self-heal, `_ensure_native_terminal_for_turn`, for reaped panes). Eager launch
is load-bearing for fork features — in particular Smart Routing, whose loopback
routers must be advertised in the bridge dir *before* the CLI boots so its
hooks can find them.

```
POST /v1/sessions
  └─ _initialize_session
      ├─ resolve spec, session-init envelope (workspace/labels/model/effort)
      └─ _launch_native_terminal  (provider seam: harness key → hooks)
          ├─ pre-launch gates (transfer-inbound check, needs-terminal)
          └─ _launch_<key>  →  _auto_create_<key>_terminal
              ├─ prepare_bridge_dir, clear_bridge_state   ← wipes stale state.json
              ├─ start routers (Smart Routing sessions)
              ├─ start app-server (codex) / tmux pane
              ├─ write fresh state.json
              └─ start transcript forwarder (registered in _AUTO_FORWARDER_TASKS)
```

Consequence to remember: **anything pre-seeded in a bridge dir before create
is wiped by the eager launch.** Tests that simulate "a loaded bridge" must
seed state *after* the create call.

## Control-event flow (example: model change from the web UI)

```
web UI → server PATCH → control event over tunnel → runner
  runner: read labels → bridge id → state.json (socket, thread)
  runner → codex app-server: RPC thread/settings/update
  runner → server: 204
```

When no loaded bridge exists, handlers degrade deliberately and differently:
settings return 503 (a silent 204 would claim a switch the app-server never
saw); compact falls through to server-side compaction.

## Code map after the consolidation

```
omnigent/runner/app.py            ALL machinery — upstream shape + fork hooks inline
omnigent/runner/native/
  ├─ orchestration.py (~900 L)    fork-only leaf: routers, launch metadata, routed spawn
  ├─ interrupt.py                 interrupt/stop control (DI: app injects 3 callables)
  └─ __init__.py                  re-exports only
omnigent/<key>_native*.py         vendor integrations (upstream)
omnigent/harness_plugins.py       provider seam: registry mapping harness key → hooks
omnigent/native_dispatch.py         (e.g. omnigent.runner.app:_launch_codex)
omnigent/runner/subagent_routing.py   Smart Routing (fork-only)
omnigent/runner/turn_routing.py       first-message routing (fork-only)
omnigent/runner/session_init_protocol.py  versioned init snapshot (fork-only)
omnigent/agent_tasks/                 sub-agent/task subsystem (fork-only)
```

## The split rules (why they exist)

A 2026-08 upstream merge re-added inline function definitions that the
`native/` extraction had deleted from `app.py`. The local defs shadowed the
imported implementations for weeks — roughly 150 tests red with no error
pointing at the cause. The structural guard
(`tests/runner/test_app_native_structure_guards.py`) now enforces:

1. **No shadows** — every name `app.py` imports from `omnigent.runner.native`
   must be undefined at app.py's top level. A local def of the same name wins
   over the import and resurrects the stale body.
2. **One-way dependency** — `native/` never imports or references
   `runner.app`. app → native only; a back edge is a circular-import trap.
3. **Single-homed state** — mutable registries (forwarder task tables,
   app-server pools) exist in exactly one module. Duplicated registries
   cause split-brain: register writes dict A, cancel pops dict B, sessions
   leak live tasks.
4. **Orchestration stays leaf** — upstream-derived defs belong in `app.py`
   (checked against `origin/main`), so upstream merges are text-merges.

Rule of thumb for placement: **if upstream also has the function, it lives in
`app.py`** (so upstream's edits merge as text); **if it is fork-only and
referenced by nothing in app.py's machinery, it may live in the leaf layer.**
State dicts always co-locate with the functions that mutate them.

## Fork-vs-upstream provenance: the recurring trap

Several long-red tests are upstream tests copied verbatim into fork-side
files, encoding upstream's *timing* while the fork changed *when* behavior
fires. Concrete example: the codex settings/compact/interrupt tests seed
bridge state before session create (upstream's pattern, valid under
upstream's lazy launch), but the fork's eager launch clears that state during
create — so the seeded state is gone by the time the event arrives. When a
fork PR changes when inherited behavior fires, sweep the upstream-inherited
tests in that area for pre-seeding patterns.
