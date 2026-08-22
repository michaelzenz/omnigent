# PuppyGarden roles and worker providers

Status: accepted

This document replaces PuppyGarden's current role-backed worker model. It separates three concepts that are currently mixed together:

1. A **PuppyGarden role** defines a manager, broker, or secretary manual.
2. A **Worker Provider** describes how PuppyGarden can initialize a kind of worker.
3. A **Worker** is one durable, initialized-or-initializing runtime instance created from a provider.

The design deliberately does not require external workers to behave like Omnigent New Session targets. Internal providers may reuse New Session infrastructure; external providers use their own adapters and configuration.

## Goals

- Run manager, broker, and secretary roles on OmniHarness so they share OmniHarness infrastructure such as memory.
- Represent each role's manual as a PromptProfile rather than an agent bundle or execution target.
- Keep role PromptProfiles out of New Session and PromptProfile Auto Select.
- Retain custom manager roles, while making the seeded default manager non-deletable.
- Remove manager agent import and private role backing forks.
- Replace prompt-bearing worker roles with prompt-free Worker Providers.
- Let the manager discover which providers it can use to create workers.
- Give every Worker a small, target-independent lifecycle and adapter contract.
- Keep Agent Queue responsible for queueing, cancellation policy, and retries.
- Support external workers without requiring PuppyGarden to collect or route external approval/input responses.

## Non-goals

- Designing the final external-provider registration or credential schema.
- Reusing the standalone Chat page for PuppyGarden.
- Routing external approval or elicitation responses through PuppyGarden.
- Rewind, steering, model switching after initialization, and other enhanced controls as minimum Worker requirements.
- Preserving existing PuppyGarden tasks or task items. The system is not in use, so rollout will reset that data manually.

## Terminology

| Term | Meaning |
| --- | --- |
| PromptProfile | A named manual/instruction document consumed by OmniHarness. |
| Role definition | A lightweight binding from a PuppyGarden role key to a PromptProfile. |
| Manager role | A user-selectable manager manual. All manager roles execute on OmniHarness. |
| Worker Provider | A reusable definition of how to initialize a Worker. It contains no prompt. |
| Worker | A PuppyGarden-owned runtime handle created from a Worker Provider. |
| Worker adapter | The integration that initializes and observes a target system. |
| Target ID | The durable session/thread/conversation ID assigned by the target system. |

## 1. PromptProfiles and role visibility

### 1.1 PromptProfile identity

PromptProfile already has a UUID-backed `id` primary key separate from `name`. The name is display data and may change without changing profile identity.

Add one column:

```python
visible: bool = True
```

Semantics:

- `visible=True`: appears in general PromptProfile pickers and may participate in Auto Select.
- `visible=False`: hidden from general discovery and Auto Select, but still usable through an explicit profile ID.
- `enabled`: independently determines whether the profile may be used.
- `archived`: independently determines whether the profile has been retired.

General PromptProfile discovery and Auto Select must require:

```text
enabled = true
archived = false
visible = true
```

Explicit lookup by ID must not require `visible=true`.

### 1.2 Role definitions

Retain only a lightweight role binding:

```text
PuppyGardenRoleDefinition
  role_key
  kind
  prompt_profile_id
  created_at
  updated_at
```

Supported kinds initially are:

```text
manager
broker
secretary
```

The old role fields are removed:

```text
agent_profile_id
harness
model
host_id
workspace
description
```

`description`, display name, and manual text come from the bound PromptProfile. Runtime model selection belongs to the role conversation, not the role manual.

A role is still addressed internally by `role_key`, while the UI always displays its PromptProfile name.

### 1.3 Seeded roles

Seed these non-deletable definitions:

```text
manager:default
broker
secretary
```

Each definition points to an ordinary generated PromptProfile UUID. No deterministic PromptProfile UUID is needed because the role definition persists the binding.

The bound PromptProfiles are created with `visible=false`.

### 1.4 Manager roles

Custom manager roles remain supported.

Creating a manager role:

1. Creates a hidden PromptProfile containing its name, description, and manual.
2. Creates a `manager:*` role definition bound to that profile.
3. Makes it available in the manager-role picker.

Deleting a custom manager role:

1. Rejects deletion while a task references it.
2. Deletes the role definition.
3. Archives the hidden PromptProfile.

The seeded `manager:default` role cannot be deleted, but its PromptProfile name, description, and manual may be edited. The PromptProfile name is the manager role's display name; there is no separate role display-name field.

Tasks retain `manager_role_key` because a task may select any manager role.

### 1.5 Role execution

Manager, broker, and secretary sessions always use:

```text
execution target = OmniHarness
prompt profile mode = fixed
prompt profile ID = selected role definition's prompt_profile_id
```

This makes the role manual available to OmniHarness and allows roles to use OmniHarness memory and related infrastructure.

The role UI may change the session model. It does not allow changing the role's execution target or harness.

## 2. Remove manager import and backing agents

Remove the current role-agent import path and all supporting concepts:

```text
POST /v1/agent-tasks/roles/{role}/import-agent
candidate_agents
private role backing forks
packaged-agent import into a role
manager agent-profile selection
manager harness selection
manager host/workspace configuration
```

Remove the corresponding frontend import control and API client method.

Custom manager role creation remains, but it creates a hidden PromptProfile and role definition rather than an agent fork.

## 3. Worker Providers

### 3.1 Definition

A Worker Provider answers:

> How can PuppyGarden initialize this kind of worker?

It contains no system prompt, PromptProfile, or copied worker instructions.

```text
WorkerProvider
  id
  name
  description
  kind
  configuration
  created_at
  updated_at
```

Provider identity is separate from Worker identity. A provider is a reusable definition; a Worker is a runtime instance created from it.

### 3.2 Default internal provider

Seed one non-deletable provider:

```text
name: Default Worker
kind: internal
```

The provider is editable but cannot be deleted. The user may choose its internal execution target and supported launch settings.

The provider has no prompt. Work instructions come from the manager and Agent Queue when a task item is dispatched.

Custom Worker Providers may also be created. This allows the manager to choose among configurations such as a general OmniHarness provider, a Codex coding provider, or a Claude Code review provider without reintroducing worker prompts.

### 3.3 Internal providers

Only internal providers reuse New Session execution-target infrastructure.

Their settings use the existing New Session concepts:

- Execution target.
- Host when required.
- Workspace when required.
- Model when supported.

The existing session API launches by `agent_id`, so internal provider configuration must retain the concrete execution-target selection rather than only a raw harness string.

An initial internal configuration may look like:

```json
{
  "kind": "internal",
  "agent_id": "ag_...",
  "host_id": "host_...",
  "workspace": "/repo",
  "model": "..."
}
```

Reuse extracted New Session controls and catalogue hooks, not the entire New Session dialog. Provider configuration must not inherit unrelated behavior such as navigation, initial chat messages, or project preferences.

Model controls are capability-driven. For example, show the OmniHarness model selector when OmniHarness supports that override.

### 3.4 External providers

External providers do not use the New Session execution-target selector.

They use adapter-specific configuration, for example:

```json
{
  "kind": "external",
  "adapter": "external-application",
  "configuration": {
    "url": "https://example.invalid/..."
  }
}
```

The exact external target, URL, application, credential, discovery, and registration model is deferred until the first external integration is designed.

An external adapter supplies its own:

- Configuration schema or UI.
- Availability/readiness.
- Supported capabilities.
- Initialization implementation.
- Activity and response-request observation.
- Optional link for opening the external application.

### 3.5 Provider availability and capabilities

A provider exposes whether it can initialize a new Worker now:

```text
available
unavailable
requires_authentication
runner_offline
configuration_required
```

It also exposes a concrete unavailable reason.

Capabilities are reported by the selected adapter/target and are not user-editable. Minimum modern Worker capabilities are:

```text
initialize asynchronously
multi-turn messaging
streamed output
final result
failure reason
interrupt current turn
terminate session
observe approval/input requests
observe approval/input request clearing
runner disconnect detection
resume/rebind
durable transcript retrieval
idempotent initialize and send
explicit operation timeout
```

Optional extensions include:

```text
inline response routing
rewind
live steering
attachments
rich tool progress
mid-session model changes
```

### 3.6 Manager discovery

The manager sees Worker Providers, not every raw execution target.

The manager can:

```text
list_worker_providers()
create_worker(provider_id)
initialize_worker(worker_id)
```

Provider summaries include:

```text
provider ID
name
description
availability
unavailable reason
capabilities
```

The user may request a provider by display name in the manager prompt. The manager resolves it to a provider ID and reports unavailable or ambiguous choices instead of guessing.

## 4. Workers

### 4.1 Identity

A Worker has two runtime identities:

```text
worker_id
target_id
```

- `worker_id` is PuppyGarden's durable internal ID and exists immediately.
- `target_id` is assigned by the target system during initialization and is nullable before initialization.

For an internal Omnigent target, `target_id` is the Omnigent conversation ID. For an external target, it is the external application's durable thread/session ID.

Store `target_id` as text rather than `Uuid16`, because external IDs may not be UUID-shaped.

### 4.2 Provider snapshot

`create_worker(provider_id)` reads the selected provider and snapshots its display and launch configuration into the Worker. The Worker does not need a live provider reference after creation.

This ensures that editing or deleting a provider after Worker creation cannot change how an uninitialized Worker starts.

The provider ID is a create-operation input, not an additional Worker runtime identity.

A conceptual Worker record is:

```text
Worker
  worker_id
  task_id
  target_id
  state
  needs_response
  provider_name_snapshot
  provider_configuration_snapshot
  failure_reason
  last_observed_at
  created_at
  updated_at
```

### 4.3 Lifecycle

Worker state is:

```text
uninitialized
initializing
idle
busy
disconnected
initialization_failed
terminated
```

Definitions:

- `uninitialized`: the internal Worker exists, but no target session exists.
- `initializing`: target initialization is running asynchronously.
- `idle`: the target can accept a new normal message.
- `busy`: the target must not receive another normal message.
- `disconnected`: the target/runner cannot currently be observed.
- `initialization_failed`: initialization ended without a usable target.
- `terminated`: the target session has been permanently closed.

`needs_response` is independent of lifecycle state:

```text
state = busy
needs_response = true
```

UI priority is:

```text
needs_response         -> Needs response
initializing           -> Starting
busy                   -> Working
disconnected           -> Disconnected
idle                   -> Idle
initialization_failed  -> Failed to start
terminated             -> Terminated
```

Completed, failed, cancelled, and interrupted are dispatch/queue-item outcomes, not ordinary Worker terminal states. A multi-turn Worker normally returns to `idle` after a turn.

### 4.4 Creation and initialization

Worker creation is synchronous and fast:

```http
POST /v1/agent-tasks/{task_id}/workers
{
  "provider_id": "..."
}
```

It returns the durable Worker ID immediately:

```json
{
  "worker_id": "...",
  "target_id": null,
  "state": "uninitialized"
}
```

Initialization is separate and asynchronous:

```http
POST /v1/task-workers/{worker_id}/initialize
```

It atomically changes `uninitialized` to `initializing`, schedules adapter initialization, and returns `202` without waiting for the target session.

On success, the adapter saves `target_id` and reports `idle`. On failure, it reports `initialization_failed` with a durable failure reason.

Initialization is idempotent by `worker_id`:

- `uninitialized`: start initialization.
- `initializing`: return the existing operation.
- `idle` or `busy`: return the initialized Worker.
- `initialization_failed`: require an explicit retry.
- `terminated`: reject.

## 5. Worker adapter contract

The common adapter is conceptually:

```python
class WorkerAdapter(Protocol):
    async def initialize(worker, provider_snapshot) -> str: ...
    async def send(worker, dispatch_id, message) -> None: ...
    async def interrupt(worker, dispatch_id) -> None: ...
    async def terminate(worker) -> None: ...
    async def observe(worker) -> WorkerObservation: ...
    async def rebind(worker) -> None: ...
```

`initialize` returns the target system's durable `target_id`.

`send` receives the Agent Queue item ID as an idempotency key. Retrying transport must not execute the same dispatch twice.

Observation exposes at least:

```text
connected
idle or busy
needs_response
response notice when available
streamed output
final result
failure reason
cancellation
```

Streaming may be push-based, but every adapter must provide an authoritative snapshot for restart and recovery.

### 5.1 Internal adapter

The initial adapter uses existing Omnigent session infrastructure:

1. Create the selected internal execution-target session.
2. Save its conversation ID as `target_id`.
3. Ensure its runner starts.
4. Translate session activity to Worker activity.
5. Stream output and terminal turn results.
6. Rebind using the conversation ID after runner reconnect.

Internal session status maps approximately as:

```text
running / waiting / launching -> busy
idle                          -> idle
runner unavailable            -> disconnected
```

Pending elicitation or approval produces:

```text
busy = true
needs_response = true
```

### 5.2 External response flow

PuppyGarden does not need to route an external response.

The external flow is:

1. Adapter observes a pending approval or input request.
2. PuppyGarden displays `Needs response`.
3. User opens the external application and responds there.
4. Adapter observes that the request disappeared.
5. PuppyGarden clears `Needs response`.
6. If the target is still executing, the Worker remains `busy` and displays `Working`.
7. When the adapter observes `idle`, Agent Queue may dispatch the next message.

Possible transitions are:

```text
Needs response -> Working -> Idle
Needs response -> Idle
```

Internal targets may later add inline response routing as an optional extension.

## 6. Agent Queue ownership

Agent Queue continues to own:

- Message ordering.
- Queued work.
- One-at-a-time dispatch.
- Queued and active dispatch cancellation policy.
- Retry policy.
- Queue pause/halt state.
- Dispatch timeout policy.

The Worker adapter owns the target primitive required to enforce those decisions, such as interrupting the current target turn.

A queue item may dispatch only when:

```text
queue.state == active
queue.inflight_item_id is null
worker.state == idle
worker.needs_response == false
```

Dispatch flow:

1. Agent Queue leases the queue and marks one item in flight.
2. It calls `adapter.send(worker, queue_item.id, message)`.
3. The adapter/observation feed reports the Worker as busy.
4. The queue item remains in flight while the target runs or needs a response.
5. Final result or failure settles the queue item.
6. The Worker returns to idle when the target can accept another message.
7. The idle observation wakes the dispatcher.

Cancellation and retry are not separate Worker features:

- Cancelling queued work never calls the adapter.
- Cancelling active work causes Agent Queue to call `adapter.interrupt`.
- Retry remains an Agent Queue operation.

## 7. APIs

### 7.1 Role APIs

Retain manager-role CRUD, but change it to manage hidden PromptProfiles and role bindings.

```text
GET    /v1/agent-tasks/roles/profiles?kind=manager
POST   /v1/agent-tasks/roles/manager
PATCH  /v1/agent-tasks/roles/{role}
DELETE /v1/agent-tasks/roles/{role}
```

The default manager delete path returns a conflict. Broker and secretary definitions are also protected.

Remove:

```text
POST /v1/agent-tasks/roles/{role}/import-agent
candidate-agent APIs and response fields
worker-role CRUD
worker-role reassignment
```

### 7.2 Worker Provider APIs

```text
GET    /v1/worker-providers
POST   /v1/worker-providers
GET    /v1/worker-providers/{provider_id}
PATCH  /v1/worker-providers/{provider_id}
DELETE /v1/worker-providers/{provider_id}
```

The seeded default provider cannot be deleted.

### 7.3 Worker APIs

```text
POST   /v1/agent-tasks/{task_id}/workers
GET    /v1/task-workers/{worker_id}
POST   /v1/task-workers/{worker_id}/initialize
POST   /v1/task-workers/{worker_id}/interrupt
DELETE /v1/task-workers/{worker_id}
```

Normal work messages continue through Agent Queue rather than a second Worker-specific queue.

## 8. UI

### 8.1 Manager roles

Keep:

- Create custom manager role.
- Manager-role picker for tasks.
- Rename and manual editing.
- Delete custom manager role.

Remove:

- Manager agent import.
- Candidate-agent selection.
- Manager execution-target selector.
- Manager harness selector.
- Manager host/workspace controls.

The role name shown in all UI is the bound PromptProfile name. The seeded default may be renamed but not deleted.

### 8.2 Worker Providers

Rename the existing worker-role area to `Worker Providers`.

Provider UI shows:

- Name and description.
- Provider kind.
- Availability and failure reason.
- Internal New Session-style controls for internal providers.
- Adapter-specific configuration for external providers.
- Model control when supported.
- Delete action for custom providers only.

Remove all worker prompt/manual editing.

### 8.3 Tasks and workers

Replace worker-role selection with Worker Provider selection when creating a Worker. Once created, the Worker uses its immutable provider snapshot; changing provider means creating another Worker.

Worker UI shows:

- Provider name snapshot.
- Starting, Working, Needs response, Idle, Disconnected, or Failed state.
- Streamed/final output.
- Failure reason.
- Interrupt and terminate actions.
- Optional `Open external app` action when an external adapter provides a URL.

## 9. Destructive manual reset

Do not write a compatibility migration for existing PuppyGarden tasks. Before enabling the new model, manually erase existing PuppyGarden operational data, including dependent rows, in foreign-key-safe order.

The reset must cover at least:

```text
agent_queue_items and PuppyGarden agent_queues
task_event_executions
task_item_events
task_items
workers
task_assets
task tags and routing attempts
task events
tasks
user role sessions
legacy task role profiles
```

Also remove private backing agents/forks created exclusively for PuppyGarden roles. Do not remove ordinary user PromptProfiles or unrelated agents.

After reset:

1. Seed hidden PromptProfiles for manager, broker, and secretary.
2. Seed protected role definitions bound to those profiles.
3. Seed the non-deletable default internal Worker Provider.
4. Start with no tasks, task items, Workers, or queue items.

The reset must be an explicit maintenance operation and fail loudly on partial cleanup; it must not silently run on every server startup.

## 10. Schema direction

### Keep or introduce

```text
prompt_profiles.visible
puppygarden_role_definitions(role_key, kind, prompt_profile_id, timestamps)
worker_providers(id, name, description, kind, configuration, timestamps)
workers(worker_id, task_id, target_id, state, needs_response,
        provider snapshots, failure metadata, observation timestamps)
tasks.manager_role_key
task_items.worker_id
```

### Remove

```text
tasks.worker_role_key
workers.role_key
workers.agent_profile_id
workers.session_id
workers.external_session_hint
legacy TaskRoleProfile launch fields
worker_role_key in queue payloads
role backing agent/fork machinery
```

Adoption-specific fields may remain temporarily only if the existing external-adoption path still needs them while external Worker Providers are being designed.

## 11. Delivery plan

### Phase 1: PromptProfiles and role manuals

- Add `PromptProfile.visible`.
- Filter general discovery and Auto Select by visibility.
- Add lightweight role-to-PromptProfile bindings.
- Seed hidden default manager, broker, and secretary manuals.
- Fix these roles to OmniHarness.
- Confirm role sessions receive OmniHarness memory.

### Phase 2: Manager simplification

- Keep custom manager creation and manager selection.
- Create hidden PromptProfiles for custom managers.
- Display PromptProfile names as manager role names.
- Protect the seeded default manager from deletion.
- Remove role import, candidate agents, and private backing forks.
- Remove manager launch fields other than session model configuration.

### Phase 3: Destructive PuppyGarden reset

- Add and review the explicit reset operation.
- Clear existing tasks, task items, Workers, queues, and legacy worker roles.
- Remove obsolete PuppyGarden backing agents safely.
- Seed the new role definitions and default Worker Provider.

### Phase 4: Worker Providers

- Add Worker Provider entity, store, and APIs.
- Seed the non-deletable default internal provider.
- Extract reusable New Session controls for internal providers.
- Add provider list/create/edit/delete UI.
- Remove worker-role prompt and import UI.
- Present provider catalogue and capabilities to the manager.

### Phase 5: Worker lifecycle and internal adapter

- Replace role/session-centric Worker fields with the new lifecycle fields.
- Add provider snapshots.
- Add create, initialize, interrupt, and terminate APIs.
- Implement the internal Omnigent adapter.
- Persist text target IDs and worker observations.
- Add runner disconnect and rebind behavior.

### Phase 6: Agent Queue integration

- Gate dispatch on authoritative Worker idle/busy state.
- Send multiple turns to the same target ID.
- Use queue-item IDs as send idempotency keys.
- Keep needs-response work in flight.
- Wake Agent Queue on idle transitions.
- Remove remaining worker-role dispatch and per-item session creation.

### Phase 7: External providers

- Define the first external provider's registration and configuration.
- Implement its adapter.
- Observe external response requests and their clearing.
- Add an external-application link where available.
- Keep external response entry outside PuppyGarden.

## 12. Test plan

### PromptProfiles and roles

- Hidden profiles do not appear in general pickers or Auto Select.
- Explicit hidden-profile selection works.
- Existing user profiles default to visible after migration.
- Default manager/broker/secretary definitions are seeded idempotently.
- Default manager may be renamed but not deleted.
- Custom manager role creation creates a hidden PromptProfile.
- Custom manager role deletion archives its PromptProfile.
- In-use manager roles cannot be deleted.
- Every manager role launches OmniHarness with the selected fixed PromptProfile.
- Manager import and candidate-agent endpoints no longer exist.

### Worker Providers

- Default internal provider is seeded and non-deletable.
- Provider name/configuration can be edited.
- Custom providers can be created and deleted.
- Internal provider validation uses the selected execution target's capabilities.
- External providers do not require an Omnigent `agent_id` or New Session configuration.
- Manager provider listing reports availability, reason, and capabilities.

### Worker lifecycle

- Worker creation returns `worker_id` immediately and leaves `target_id` null.
- Initialization is asynchronous and idempotent.
- Successful initialization stores the target ID and reaches idle.
- External-shaped, non-UUID target IDs are accepted.
- Initialization failure persists a reason.
- Provider edits after Worker creation do not alter the Worker snapshot.
- Disconnect/rebind preserves Worker identity and target ID.
- Terminated Workers cannot accept initialization or work.

### Activity and response requests

- Busy Worker blocks another Agent Queue dispatch.
- Idle transition wakes Agent Queue.
- Pending approval/input sets `needs_response` while the Worker remains busy.
- Adapter-observed request clearing clears `needs_response` without implying idle.
- External responses are not routed through PuppyGarden.
- Streamed output, final result, cancellation, and failure reason are surfaced.

### Agent Queue

- Only one item is dispatched at a time.
- Send is idempotent by queue-item ID.
- Queued cancellation never calls the adapter.
- Active cancellation calls adapter interrupt.
- Retry remains an Agent Queue operation.
- A Worker is reused across multiple queue items.
- A response request keeps the current item in flight.

### Reset

- The reset removes all PuppyGarden operational rows without touching ordinary PromptProfiles or unrelated agents.
- The reset can be verified as complete and fails loudly on partial cleanup.
- Fresh seeding produces exactly the protected role definitions and default Worker Provider.

## 13. Definition of done

The redesign is complete when:

- Manager, broker, and secretary always execute on OmniHarness.
- Each role manual is a hidden PromptProfile.
- The PromptProfile name is the role's display name.
- The seeded default manager is editable but not deletable.
- Custom manager roles can be created, selected, and deleted when unused.
- Manager agent import and private role backing forks are gone.
- Worker roles no longer contain prompts and are renamed/replaced by Worker Providers.
- The default internal provider is seeded and non-deletable.
- Internal providers reuse New Session execution-target controls; external providers do not.
- The manager discovers providers and uses one to create an uninitialized Worker.
- Worker creation returns a stable Worker ID immediately.
- Initialization asynchronously assigns the target system's target ID.
- Worker adapters authoritatively expose idle/busy, response-request, output, failure, disconnect, and rebind behavior.
- Agent Queue dispatches only when a Worker is idle and keeps cancellation/retry ownership.
- External users may resolve approvals/input entirely in the external application, after which the adapter clears PuppyGarden's Needs response state.
- Legacy PuppyGarden tasks, task items, queues, Workers, and worker roles have been explicitly reset.

## Current implementation grounding

This proposal was prepared against repository HEAD `f6148caf5f4d0073777f87c43bc3be23d74ccd0e` (`2026-08-21T19:50:02-07:00`). At that revision:

- PromptProfile uses `(workspace_id, id)` as its primary key and stores `name` separately.
- `TaskRoleProfile` mixes agent, harness, model, host, workspace, and role metadata.
- Manager and worker roles support private backing forks and agent import.
- Worker lanes resolve through `role_key` and create Omnigent sessions from role launch fields.
- Agent Queue separately tracks queue state and `inflight_item_id`.
- Existing session activity already treats pending elicitation as a busy signal.
