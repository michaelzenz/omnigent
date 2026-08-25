# Onih Dual Executor Design

## Status

Proposed implementation plan, revised after design review.

This revision intentionally chooses reconstruction over persistent-checkpoint
resume. Onih is canonical; a live Pi process owns its working context, but every
new Pi process starts from a freshly reconstructed Pi session built from Onih.
Persistent-checkpoint resume may be added later only as a performance
optimization.

## Summary

Onih is exposed as two independent execution targets:

- `onih-openai-agents` — Onih backed by the OpenAI Agents SDK.
- `onih-pi` — Onih backed by Pi's RPC agent loop.

Both targets share Onih product behavior: settings, model selection, Prompt
Profiles, persistent memory, tools, MCP, policies, projects, durable history,
and usage reporting. They differ only in the underlying harness and the
harness-owned working-context behavior.

The ownership rule is:

> Onih owns durable product state and governance. A live harness owns its agent
> loop and working context. A newly started harness reconstructs its working
> state from canonical Onih history.

For `onih-pi`, Pi writes a native per-conversation session while its process is
alive. Onih independently persists the product transcript. Onih does not try to
transactionally synchronize the database and Pi files after each turn. When a
Pi process starts for any reason, Onih builds a fresh Pi-native session from
canonical Onih state in a staging directory and atomically replaces the old Pi
session before launch.

Switching between the two Onih targets is supported. The destination executor
is reconstructed from canonical Onih history; executor-private state is never
merged bidirectionally.

## Goals

1. Keep the current OpenAI Agents implementation available as
   `onih-openai-agents`.
2. Add `onih-pi` without rebuilding Onih settings and product features around
   Pi.
3. Keep the Onih database as the canonical product conversation source of
   truth.
4. Preserve Pi's native loop, structured tool-result context, retry behavior,
   and compaction behavior while a Pi process is alive.
5. Reconstruct a fresh Pi-native session from Onih whenever a Pi process
   starts, including after restart, target switch, or host switch.
6. Support switching a conversation between `onih-openai-agents` and
   `onih-pi` while preserving canonical history.
7. Isolate `onih-pi` from ambient user and workspace Pi configuration.
8. Configure Pi for Databricks directly from Onih without requiring `ucode` at
   runtime.
9. Keep Onih's Pi provider generation aligned with `databricks/ucode` through
   focused tests and documentation.
10. Preserve existing behavior for ordinary `harness: pi` agents unless a
    generic, backward-compatible improvement is explicitly enabled.

## Non-goals

- Automatically migrate the existing `omniharness` database record.
- Resume an old Pi checkpoint as a correctness requirement.
- Transactionally commit Onih database rows and Pi session files together.
- Merge divergent Onih and Pi histories; Onih always wins.
- Treat Pi's session as a user-visible or independent product transcript.
- Replace or customize Pi's compaction algorithm.
- Make Pi-native tools or Pi-native skills available to `onih-pi`.
- Automatically retry an ambiguously interrupted whole turn.
- Benchmark or rank the two executors.
- Optimize model changes with Pi RPC `set_model` in the initial implementation.
- Make Prompt Profile transport identical across both executors initially.

## Target and settings identity

Target identity and settings identity are separate:

```python
ONIH_SETTINGS_KEY = "omniharness"

ONIH_OPENAI_AGENTS_TARGET = "onih-openai-agents"
ONIH_PI_TARGET = "onih-pi"

ONIH_TARGET_NAMES = frozenset(
    {
        ONIH_OPENAI_AGENTS_TARGET,
        ONIH_PI_TARGET,
    }
)
```

Centralize target-family checks:

```python
def is_onih_target_name(name: str | None) -> bool: ...
def is_onih_agent(agent: Agent | None) -> bool: ...
def is_onih_spec(spec: AgentSpec | None) -> bool: ...
```

Use target identity for executor selection and Onih-specific capabilities. Keep
`ONIH_SETTINGS_KEY` for shared settings and the allowed model list.

Do not infer execution capabilities from provider family. In particular,
`harness: pi` is multi-provider and must not be labeled Anthropic, OpenAI, or
Gemini merely to pass a UI eligibility predicate.

## Built-in targets

### `onih-openai-agents`

```yaml
spec_version: 1
name: onih-openai-agents
description: Onih using the OpenAI Agents SDK.

executor:
  type: omnigent
  config:
    harness: openai-agents
    model: <configured-default>
```

This target preserves current OmniHarness behavior apart from its name.

### `onih-pi`

```yaml
spec_version: 1
name: onih-pi
description: Onih using the Pi agent runtime.

executor:
  type: omnigent
  config:
    harness: pi
    model: <configured-default>
    persistent_session: true
    system_prompt_mode: replace
    isolated_resources: true
    native_tools: false
    native_skills: false
```

The exact option names may follow existing agent-spec conventions, but their
semantics must be explicit and opt-in.

## Preserve ordinary Pi behavior

`onih-pi` uses the existing Pi RPC transport, but Onih-specific behavior must
not become the default behavior of ordinary Pi agents.

Add typed, opt-in launch options such as:

```python
@dataclass(frozen=True)
class PiLaunchOptions:
    persistent_session: bool = False
    session_dir: Path | None = None
    system_prompt_mode: Literal["append", "replace"] = "append"
    isolated_resources: bool = False
    native_tools: bool = True
    native_skills: bool = True
```

With defaults, ordinary Pi continues to use its current launch behavior,
including `--no-session`, append-system-prompt behavior, and existing resource
handling. `onih-pi` supplies the non-default isolated configuration.

Avoid target-name branches inside `PiExecutor`. Generic Pi improvements are
allowed when backward-compatible, including propagating native `toolCallId`,
parsing compaction events, and accepting an optional caller-owned session path.
Do not change `pi-native`; it is a separate interactive/TUI harness.

Regression tests must snapshot ordinary Pi's launch arguments, environment,
resource behavior, tool exposure, and first-turn history behavior before and
after this work.

## Manual database rename

There is one user today, so migration code is unnecessary.

Deployment procedure:

1. Stop the server and runner.
2. Back up the database.
3. Rename the existing `omniharness` agent to `onih-openai-agents` while
   retaining its stable agent ID.
4. Verify existing conversations still reference that agent ID.
5. Start the updated code.
6. Let built-in seeding create `onih-pi`.
7. Verify old conversations open under `onih-openai-agents`.

Do not add legacy aliases, hidden legacy targets, or automatic migration logic.
The UI must hide any legacy `omniharness` record from new target selection while
still resolving old conversation metadata by ID during the manual rollout.

## Runtime ownership

| Concern | Onih | OpenAI Agents SDK | Pi |
|---|---:|---:|---:|
| Product settings | Owns | Consumes | Consumes |
| Allowed models and smart routing | Owns | Consumes | Consumes |
| Canonical conversation history | Owns | No | No |
| Live agent loop | No | Owns | Owns |
| Live working context | No | Owns | Owns |
| Pi session during a live process | Governs location/lifecycle | N/A | Owns |
| New-process reconstruction | Owns source/conversion | Replays Onih | Opens rebuilt session |
| Prompt Profile selection | Owns | Receives | Receives |
| Persistent memory retrieval | Owns | Receives | Receives |
| Tool and MCP registry | Owns | Receives | Receives through bridge |
| Tool policy, approval, execution | Owns | Delegates | Delegates through bridge |
| Compaction mechanics | Persists recovery metadata | Harness-specific | Owns |

## Pi process and storage model

### Two stores with different authority

```text
Onih database
└── canonical product transcript, policies, audit events, attachments, usage,
    and Pi compaction recovery metadata

Pi session directory
└── disposable executor working state for the currently running Pi process
```

Pi's session is useful while the process is alive, but it is not trusted as the
source for a later process start. A restart may rebuild unnecessarily; it must
never resume known-stale state.

Deleting an Onih conversation removes all associated Pi session directories.
Retention and privacy cleanup cover both stores.

### Per-conversation Pi directories

```text
<omnigent-data>/pi/onih/sessions/<hashed-conversation-id>/
├── active/
└── lock
```

Requirements:

- Encode or hash conversation IDs before using them as path components.
- Create directories with mode `0700`.
- Never share a Pi session directory between conversations.
- Use Pi's supported native session format.
- Do not use `--no-session` for `onih-pi`.
- Keep a host-local exclusive lock while a Pi process owns the active session.
- Construct replacements under a sibling staging directory.
- Validate the staged session with Pi before atomically replacing `active/`.
- A failed reconstruction leaves canonical Onih history unchanged.

The lock protects against local restart/orphan overlap. Normal conversation
turn serialization continues to use the existing runner and harness process
manager; do not introduce a second concurrent-turn mechanism.

### Shared isolated configuration

Generated configuration may be shared by sessions with the same non-secret
configuration fingerprint:

```text
<omnigent-data>/pi/onih/config/<fingerprint>/
├── models.json
├── settings.json
├── credential-helper
└── extensions/
    └── onih_bridge.js
```

The fingerprint covers at least:

- Provider names, model catalog, wire APIs, and routes
- Pi settings
- Bridge extension version
- Tool-schema/bridge protocol version
- Isolation flags
- Pi session-format compatibility
- Credential-helper identity, host, and profile, but never a bearer token

Shared configuration must not contain per-conversation turn context or bridge
tokens. Initialize each fingerprint directory under a short host-local lock.
The first process builds and validates it in staging, then installs it with an
atomic rename; concurrent processes wait and reuse the validated result. If an
existing directory does not match its fingerprint, replace it atomically from a
validated staging build. A failed repair aborts Pi startup rather than using or
falling back to mismatched configuration.

## Process startup and reconstruction

Every new `onih-pi` process starts from canonical Onih state. This applies to:

- First launch
- Runner or harness restart
- Model/provider restart
- OpenAI Agents to Pi switch
- Host switch
- Recovery after failed persistence or suspected corruption

Startup flow:

1. Confirm there is no active turn and acquire the host-local session lock.
2. Read a canonical Onih snapshot through the last completed turn.
3. Separate any pending user input from the completed-history boundary.
4. Build a fresh Pi-native session in a staging directory.
5. Preserve structured messages, tool calls, tool results, compaction state,
   ordering, and IDs.
6. Validate that Pi can open the staged session.
7. Atomically replace the old active Pi session.
8. Launch Pi on the rebuilt session.
9. Submit pending input once, identified by a stable Onih turn ID.

There is no resume-vs-repair decision and no per-turn checkpoint manifest. Old
Pi state may be retained for bounded diagnostics, but it is never chosen over
canonical Onih state.

### Reconstruction without prior compaction

Convert canonical Onih history into Pi-native entries:

```text
user messages
assistant messages
assistant tool calls
matching tool results
completed-turn boundary
pending input submitted after launch
```

Preserve:

- Tool name
- Arguments
- Pi/Onih tool-call ID
- Matching result ID
- Success/error/blocked status
- Bounded model-visible result content
- Original ordering

The current Pi executor does not propagate Pi's `toolCallId` on
`ToolCallRequest` or `ToolCallComplete`. Add it to generic executor-event
metadata and persist it end to end before relying on structured reconstruction.

Never silently drop an unsupported canonical item. Fail reconstruction clearly
rather than launching Pi with partial history.

### Reconstruction after compaction

Reconstruct from canonical recovery state:

```text
latest persisted Pi compaction summary and canonical compaction boundary
+ retained-tail metadata
+ canonical Onih items after that boundary
→ fresh Pi-native session
```

Do not replay history already represented by the summary. If faithful
reconstruction is impossible, fail visibly and leave Onih unchanged.

The existing flattened `Conversation so far` replay is only a compatibility
path and must not be used for `onih-pi` after structured reconstruction lands.

## Normal-turn persistence and failure handling

Onih and Pi persist independently during a live process. Do not build a
cross-store transaction or advance a per-turn manifest.

### Pi succeeds and Onih persists successfully

Continue using the same live Pi process and its native working context.

### Pi succeeds but required Onih persistence fails

Pi is ahead of canonical state. Surface the persistence failure, terminate and
mark the current Pi process unusable, and rebuild from Onih on the next start.
Do not automatically retry the ambiguous turn.

### Pi fails after Onih persisted the user item

Persist a failed/interrupted turn state as appropriate, terminate unusable Pi
state, and rebuild on the next start. Do not automatically resubmit the user
input; require an explicit user retry or another explicit product action.

### Pi writes partial state and crashes

Ignore the partial Pi state and reconstruct from Onih.

A stable Onih turn ID distinguishes completed, failed, pending, and explicitly
retried input. Whole-turn retries must not be inferred from connection timeout.
Provider-level and tool-level retries inside the same live turn remain owned by
existing retry mechanisms.

## Turn serialization and queued input

The current product intentionally permits sending while a response is active.
That input is queued or injected into the existing active turn; it must not
start another Pi process.

Rules:

- At most one active harness turn per conversation.
- At most one Pi process owns a conversation's active session directory.
- Additional supported input is queued/steered through the active process.
- Unsupported concurrent operations return `409 Conflict`.
- Target switching, host switching, and model/provider restart are rejected
  while a turn is active.
- The frontend prevents accidental duplicate local submission, but the server
  remains authoritative.

Reuse existing harness active-turn serialization and per-conversation process
manager locks. Do not add a renewable distributed execution lease initially.

## Host switching

A host switch is a controlled ownership handoff, not a checkpoint copy:

1. Require the conversation to be idle.
2. Stop/cancel the old host's harness process.
3. Increment the conversation's execution generation.
4. Assign the new host.
5. Reconstruct a fresh Pi session on the new host from Onih.
6. Reject output carrying an older execution generation.

The generation is a fencing token, not a renewable lease. It prevents delayed
output from an old host from becoming canonical after reassignment.

Copying Pi files between hosts is unnecessary for correctness and is not part
of the initial implementation.

## Switching between Onih executors

Switching is allowed only while the conversation is idle.

### `onih-openai-agents` to `onih-pi`

1. Stop/release the OpenAI Agents harness.
2. Bind the conversation to `onih-pi`.
3. Reconstruct a fresh Pi session from canonical Onih history.
4. Continue with the next explicit user input.

### `onih-pi` to `onih-openai-agents`

1. Ensure the current Pi turn has completed and canonical events are persisted.
2. Stop/release Pi and its host-local lock.
3. Bind the conversation to `onih-openai-agents`.
4. Let OpenAI Agents replay canonical Onih history normally.

If the user later switches back to Pi, rebuild again from Onih. Do not resume
the old Pi directory.

Expose an explicit server capability such as
`history_switch = "canonical-rebuild"`. Frontend selection must use target
capability/identity, not `harnessFamily()`.

## Pi isolation

`onih-pi` must not inherit ambient Pi behavior from:

- `~/.pi/agent/SYSTEM.md`
- `~/.pi/agent/APPEND_SYSTEM.md`
- Global Pi extensions, packages, and skills
- Project `.pi/settings.json`
- Project `.pi/SYSTEM.md` or `.pi/APPEND_SYSTEM.md`
- Project Pi extensions or skills

Launch with explicit isolation flags, including:

```text
--no-extensions
--no-skills
--no-prompt-templates
--no-context-files
--no-builtin-tools
```

Load only Onih's generated provider configuration, settings, and bridge
extension. Pi may use the selected workspace as its cwd, but filesystem and
process access must go through Onih tools.

## Prompt design

### Stable system prompt

Onih replaces Pi's built-in system prompt for `onih-pi`. Stable system
instructions include Onih behavior, policy-visible guidance, and the stable
bridge contract. They must not contain per-turn memory or profile material.

### Transient user-level context

Prompt Profile and retrieved memory are Onih-owned dynamic context. The Onih
bridge injects them into a request copy through Pi's `context` event before each
model call, including tool-loop calls.

Requirements:

- Dynamic context is not appended to Pi's native session.
- Dynamic context is not persisted as a user-authored Onih message.
- Each turn has an authenticated, isolated context payload and stable turn ID.
- Cancelled/stale payloads are rejected and cleared.
- Profile or memory changes do not require restarting Pi.

Keep rendering deterministic to preserve provider prefix caching.

## Tools and MCP

Execution path:

```text
Pi model tool call
  → generated Onih bridge tool
  → authenticated loopback bridge
  → Onih policy and approval
  → Onih tool or MCP execution
  → bounded result returned to Pi
  → full durable result persisted by Onih policy
```

Onih owns tool registration, canonical schema validation, policy, approval,
credentials, sandboxing, execution, and audit. Pi owns tool selection and
continuation of its agent loop.

Disable Pi-native `read`, `bash`, `edit`, and `write`. Do not re-enable native
`read` to support Pi skills; `onih-pi` has no Pi-native skills.

The model-facing schema may normalize unsupported JSON Schema constructs, but
Onih validates arguments against the canonical schema before execution.
Large text is bounded for model context; raw binary payloads are not embedded in
Pi history.

## Pi compaction

Pi owns compaction for `onih-pi`:

- Context estimation and thresholds
- Cut-point and retained tail
- Summary prompt and model call
- Active-context replacement
- Overflow compact-and-retry behavior

Onih must not run its generic compactor over the same Pi working context.
Manual compaction sends Pi's local RPC `compact` command.

Translate Pi events:

```text
compaction_start → CompactionStarted
compaction_end   → CompactionComplete
```

Onih persists canonical recovery metadata:

- Summary
- Canonical Onih boundary represented by the summary
- Retained-tail boundary or equivalent reconstructable metadata
- Pi internal entry IDs where available
- Reason/generation
- Tokens before/after
- Usage, model, timestamp, and failure state

Compaction is the one place where Pi-specific recovery metadata is required for
future reconstruction. If required compaction persistence fails, terminate the
current Pi process and mark the operation failed; its compacted private state
must not become the basis of future turns.

### Known limitation: retained thinking blocks

The live Pi adapter supports `thinking_start` and `thinking_delta` events, but
post-compaction recovery does not currently convert Pi `thinking` content
blocks returned by RPC `get_messages`. Thinking covered by Pi's compaction
summary needs no separate restoration: Onih persists and restores that summary.
The limitation applies only to a thinking block in Pi's retained tail, outside
the summarized boundary.

Keep the current fail-visible behavior for the initial implementation. If the
retained tail contains a `thinking` block, recovery export fails rather than
silently dropping it; required-persistence failure handling then discards the
private Pi process state. Preserving retained thinking across restart is
follow-up work, not an initial correctness requirement.

Databricks does not need a `/compact` endpoint. Pi's RPC command performs an
ordinary model request through the configured provider.

## Direct Databricks provider configuration

Onih launches Pi directly; `ucode` is not a runtime dependency.

Current compatibility mappings are:

| Pi provider | Wire API | Databricks route |
|---|---|---|
| `databricks-claude` | `anthropic-messages` | `/ai-gateway/anthropic` |
| `databricks-openai` | `openai-responses` | `/ai-gateway/codex/v1` |
| `databricks-gemini` | `google-generative-ai` | `/ai-gateway/gemini/v1beta` |

Claude additionally requires:

```json
{
  "compat": {
    "supportsEagerToolInputStreaming": false
  }
}
```

Reuse existing Onih gateway discovery and model metadata. Add contract fixtures
against the `databricks/ucode` compatibility reference for provider name, base
URL, wire API, headers, compatibility flags, selector, and model metadata.

### Credential refresh

Do not persist a literal Databricks bearer token in `models.json`, Pi history,
or checkpoint data. Pi supports a leading command in `apiKey`:

```json
{
  "apiKey": "!/path/to/onih-databricks-token --profile ai-devtools-prod",
  "authHeader": true
}
```

The Onih-owned helper resolves or refreshes credentials, prints only the token
to stdout, sends diagnostics to stderr, and exits nonzero on failure. It may
cache by expiry under owner-only storage. Pi resolves provider request keys at
request time, allowing a persistent process to receive refreshed credentials.

Tests must prove successive requests can observe refreshed helper output and
that bearer tokens do not appear in configuration fingerprints, logs, Onih
items, Pi sessions, or generated metadata.

## Model selection

The model allowlist remains controlled by shared Onih settings:

```text
Onih settings
  → explicit selection or smart routing
  → selected model
  → target executor
```

For Pi, resolve provider, model ID, wire API, tool/reasoning support, image
support, context window, output limit, and usage metadata. Unsupported models
fail clearly; never silently invent a provider or fall back to another model.

The initial implementation may restart Pi when the selected model changes. A
restart reconstructs a fresh Pi session from Onih.

## UI behavior

- Hide legacy `omniharness` from all new/switch target pickers.
- Sort `onih-openai-agents` and `onih-pi` before other targets.
- Both targets use the same Onih gear settings: system prompt, subagent routing,
  and Prompt Profile.
- Both targets show the Onih model/smart-routing selector beside the execution
  target selector in new and existing chats.
- Do not show native Pi model or harness configuration for `onih-pi`.
- Existing idle Onih conversations may switch between both targets.
- Disable/reject executor switching while a turn is active.

## Observability

Record at least:

- Onih target and underlying engine
- Selected model, provider, and wire API
- System-prompt, profile, memory, and tool-schema fingerprints
- Input/output/cache tokens when reported
- Tool calls and policy outcomes
- Pi process starts/restarts and reconstruction reason
- Reconstruction source: full history or compaction recovery state
- Reconstruction failures and duration
- Compaction lifecycle and usage
- Execution generation and rejected stale-host output
- Persistence failures that caused Pi state to be discarded

Do not add comparative benchmarking.

## Implementation phases

### Phase 1: identity and UI

- Add both target bundles and centralized Onih checks.
- Preserve the shared settings/model namespace.
- Implement labels, ordering, gear/model controls, and legacy hiding.
- Add explicit canonical-rebuild switch capability.
- Perform the database rename manually.

### Phase 2: backward-compatible Pi capabilities

- Add opt-in persistent session path and system-prompt replacement.
- Add explicit resource isolation and native tool/skill controls.
- Propagate Pi `toolCallId` through generic executor event metadata.
- Parse Pi compaction events and support manual RPC compaction.
- Keep ordinary Pi defaults unchanged with regression tests.

### Phase 3: direct provider and bridge

- Generate isolated provider/settings files.
- Add dynamic credential-helper configuration and contract tests.
- Add the Onih tool bridge and transient profile/memory context protocol.
- Add turn IDs, cancellation, isolation, and stale-context cleanup.

### Phase 4: deterministic reconstruction

- Convert canonical Onih items into a staged Pi-native session.
- Reuse/extract conversion primitives from `pi_native_resume.py` without
  changing `pi-native` behavior.
- Preserve structured tool pairs and bounded model-visible results.
- Validate with Pi and atomically replace the active session.
- Reconstruct on every process start and executor/host switch.

### Phase 5: compaction recovery

- Disable Onih generic compaction for `onih-pi`.
- Persist Pi summary, canonical boundary, retained tail, usage, and generation.
- Reconstruct from canonical compaction recovery state.
- Terminate/discard Pi state when required compaction persistence fails.

### Phase 6: lifecycle hardening

- Enforce one active turn/process per conversation.
- Add local session lock and execution-generation fencing.
- Reject switching while active and stale output after host reassignment.
- Verify restart, target switch, host switch, cancellation, persistence failure,
  and explicit retry behavior.
- Couple Pi directory deletion/retention to Onih conversation lifecycle.

## Test plan

### Identity and compatibility

- Both targets are recognized as Onih targets and share settings/models.
- Legacy `omniharness` is hidden from selection.
- Ordinary Pi launch behavior is unchanged.
- `pi-native` behavior is unchanged.
- No code classifies Pi as a fake provider family.

### Switching and lifecycle

- OpenAI Agents to Pi reconstructs canonical history.
- Pi to OpenAI Agents preserves canonical history.
- Switching back to Pi rebuilds rather than resumes old private state.
- Switching is rejected while active.
- Queued follow-up input uses the current process and does not spawn another.
- Ambiguous timeout does not automatically retry the whole turn.
- Stale execution-generation output is rejected after host switch.

### Reconstruction

- Full history reconstructs structured user, assistant, tool-call, and result
  items in order.
- Tool IDs remain paired.
- Pending input is submitted exactly once after the completed boundary.
- Unsupported items fail visibly rather than being dropped.
- Staged reconstruction failure leaves the active/canonical state intact.
- Process restart always rebuilds from Onih, ignoring stale Pi data.

### Isolation

- Global/project Pi prompts, settings, extensions, and skills are ignored.
- Pi-native filesystem and shell tools are unavailable.
- Separate conversations cannot share session directories, locks, bridge state,
  or transient context.
- Directories and files have owner-only permissions.

### Provider and credentials

- Generated routes/wire APIs match compatibility fixtures.
- Unsupported models fail clearly.
- `models.json` contains a credential command rather than a literal token.
- Credential refresh works without restarting Pi.
- Secrets do not appear in logs, fingerprints, histories, or artifacts.

### Compaction

- Automatic and manual Pi compaction use configured model APIs.
- Onih does not double-compact.
- Canonical summary/boundary/tail metadata persists.
- Restart reconstructs summary plus tail without replaying summarized history.
- Persistence failure terminates/discards divergent Pi state.
- A retained-tail `thinking` block fails recovery export visibly and discards
  private Pi state; it is not silently omitted.

### UI

- Both Onih targets appear first in new/existing selectors.
- Legacy `omniharness` is absent.
- Both targets expose identical Onih gear controls.
- Both show smart routing and actual models beside the target selector.
- `onih-pi` does not expose native Pi settings.

## Local fork validation

Run entirely from the feature worktree; never copy source or build output into
the main checkout. Vite writes directly to the worktree's
`omnigent/server/static/web-ui/`.

```bash
pnpm install --frozen-lockfile --shamefully-hoist
uv sync --extra databricks
./scripts/fork_local_instance.py onih-migration
```

The fork script snapshots the database, applies migrations, builds the SPA,
starts an isolated server/host pair, launches Electron with isolated user data,
and verifies through a temporary loopback CDP endpoint that React rendered.
Electron logs live under:

```text
~/.omnigent/instances/<name>/logs/electron/
```

Stop with:

```bash
./scripts/fork_local_instance.py --stop onih-migration
```

Manual database rename remains a separate explicit operation.

## Definition of done

- Both Onih targets are independently selectable and switchable while idle.
- Existing sessions survive the manual target rename.
- Shared Onih settings, model selection, tools, policies, profiles, and memory
  apply to both targets.
- Ordinary Pi and `pi-native` behavior remain unchanged.
- `onih-pi` is isolated from ambient Pi resources and native tools.
- Onih configures Pi directly with dynamic Databricks credential refresh.
- Onih remains canonical; every new Pi process reconstructs from Onih.
- No per-turn cross-store transaction or checkpoint-resume requirement exists.
- Pi owns live compaction; Onih persists sufficient canonical recovery metadata.
- Target/host switching, restart, failure, and stale-output behavior are tested.
- Engine, usage, reconstruction, persistence failure, and compaction are
  observable.

## Follow-up work

- Optimize reconstruction only if measured restart latency warrants it.
- A future optimization may validate and resume a Pi checkpoint, but it must not
  change Onih's canonical authority or be required for correctness.
- Use Pi RPC `set_model` when provider configuration remains compatible.
- Consider extracting shared Databricks-to-Pi provider generation if ucode
  alignment becomes costly.
- Align transient profile transport across both executors if behavioral parity
  becomes important.
- Preserve Pi `thinking` blocks in post-compaction retained-tail recovery if
  restart continuity for those blocks becomes necessary.

## Source snapshot

This design is grounded against the following snapshots observed on 2026-08-24
and revised on 2026-08-25:

- `omnigent-fork` commit `70088598c3b667af01b890c44b1c8afcbdac1d3d`
- Pi commit `dcd461925db2edf69a43c8135db1180d418afd54`
- Installed Pi package `@earendil-works/pi-coding-agent` 0.79.0
- `databricks/ucode` main as observed with GitHub `pushed_at`
  `2026-08-24T23:27:36Z`

Relevant files include:

- `omnigent/inner/pi_executor.py`
- `omnigent/inner/pi_harness.py`
- `omnigent/pi_native_resume.py`
- `omnigent/runtime/harnesses/_executor_adapter.py`
- `omnigent/runtime/harnesses/_scaffold.py`
- `omnigent/runtime/harnesses/process_manager.py`
- `omnigent/runtime/prompt.py`
- `omnigent/runner/app.py`
- `web/src/lib/forkHarness.ts`
- Pi `packages/coding-agent/docs/rpc.md`
- Pi `packages/coding-agent/docs/compaction.md`
- Pi `packages/coding-agent/docs/extensions.md`
- Pi `packages/coding-agent/docs/models.md`
- Pi `packages/coding-agent/src/core/compaction/compaction.ts`
- ucode `src/ucode/agents/pi.py`
