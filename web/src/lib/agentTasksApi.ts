import { authenticatedFetch } from "@/lib/identity";

export interface AgentTaskSummary {
  id: string;
  title: string;
  description: string | null;
  state: string;
  manager_role_key: string;
  manager_id: string | null;
  manager_conversation_id: string | null;
  goal?: string;
  created_at?: number;
  updated_at?: number | null;
  priority?: number;
  queue_rank?: number;
}

export interface TaskEventSummary {
  id: string;
  event_type: string;
  title: string;
  state: string;
  payload: string | Record<string, unknown> | null;
  created_at: number;
  updated_at: number | null;
}

export interface TaskItemSummary {
  id: string;
  title: string;
  description: string | null;
  instructions: string | null;
  internal_note: string | null;
  state: string;
  worker_id: string | null;
  /** "work" (default) dispatches to a worker; "human_action" is completed by the user. */
  kind?: "work" | "human_action";
  /** Present when the server knows which agent-queue row backs this item. */
  queue_item_id?: string | null;
  created_at: number;
  updated_at: number | null;
}

export interface TaskExecutionSummary {
  id: string;
  task_item_id: string;
  event_title: string | null;
  item?: TaskItemSummary | null;
  status: string;
  result_summary: string | null;
  error: string | null;
  conversation_id: string | null;
  attempt_no: number;
  assigned_at: number;
  started_at: number | null;
  finished_at: number | null;
}

export interface TaskWorkerRowItem {
  kind: "item";
  item: TaskItemSummary;
  default_folded: boolean;
  sort_at: number;
}

export interface TaskWorkerRowExecution {
  kind: "execution";
  execution: TaskExecutionSummary;
  default_folded: boolean;
  sort_at: number;
}

export type TaskWorkerRow = TaskWorkerRowItem | TaskWorkerRowExecution;

export type TaskWorkerLaneState = "new" | "active" | "idle";

export interface TaskWorkerLane {
  worker_id: string;
  kind: string;
  target_id: string | null;
  state: TaskWorkerLaneState;
  worker_state?:
    | "uninitialized"
    | "initializing"
    | "idle"
    | "busy"
    | "disconnected"
    | "initialization_failed"
    | "terminated";
  needs_response?: boolean;
  /** Manager-maintained label of recent work; falls back to provider_name. */
  title?: string | null;
  /** Session last-update epoch seconds (item append, title change); external
   *  lanes use the watcher's last observation. Lanes sort most-recent-first. */
  last_active_at?: number | null;
  provider_name?: string | null;
  host_id?: string | null;
  workspace?: string | null;
  failure_reason?: string | null;
  situation: string;
  rows: TaskWorkerRow[];
  executions: TaskExecutionSummary[];
}

export type TaskAssetCategory = "code" | "tests" | "documents" | "logs" | "other" | "workspace";

export interface TaskAssetSummary {
  id: number;
  kind: "url" | "workspace";
  category?: TaskAssetCategory;
  title: string;
  /** URL for ``kind=url``; absolute workspace path for ``kind=workspace``. */
  url: string | null;
  /** Worker lane this asset was harvested from; null = human-added. */
  source_worker_id?: string | null;
  created_at: number;
}

export interface TaskDashboard {
  task: {
    id: string;
    title: string;
    description: string | null;
    state: string;
    manager_id: string | null;
    manager_conversation_id: string | null;
    goal?: string;
    created_at?: number;
    priority?: number;
    queue_rank?: number;
  };
  derived: {
    has_running_workers: boolean;
  };
  inbox_items: TaskItemSummary[];
  /** V2 card read model. Optional while older servers are still deployed. */
  active_items?: TaskItemSummary[];
  recent_done_items?: {
    all: TaskItemSummary[];
    by_worker: Record<string, TaskItemSummary[]>;
  };
  reconcile_queue_count: number;
  assets: TaskAssetSummary[];
  workers: TaskWorkerLane[];
}

export interface DispatchPayload {
  title?: string;
  description?: string;
  instructions?: string;
  host_id?: string;
  workspace?: string;
  harness?: string;
  model?: string;
}

export const TASK_SECRETARY_ROLE = "secretary";
export const TASK_BROKER_ROLE = "broker";
export const MANAGER_DEFAULT_ROLE_KEY = "manager:default";
export const MANAGER_ROLE_PREFIX = "manager:";

// Conversation label marking a PuppyGarden role session. Mirrors the backend
// ``omnigent.agent_tasks.session_labels`` constants. ``task_broker`` and
// ``task_manager`` are background agents whose chat is not a reading surface
// (their output lands on the PuppyGarden board), so the sidebar never shows
// their unread dot and excludes them from the unread badge count.
export const ROLE_LABEL_KEY = "omnigent.role";
export const BROKER_ROLE_VALUE = "task_broker";
export const SECRETARY_ROLE_VALUE = "task_secretary";
export const MANAGER_ROLE_VALUE = "task_manager";

export function isBrokerSession(labels: Record<string, string> | undefined): boolean {
  return labels?.[ROLE_LABEL_KEY] === BROKER_ROLE_VALUE;
}

export function isManagerSession(labels: Record<string, string> | undefined): boolean {
  return labels?.[ROLE_LABEL_KEY] === MANAGER_ROLE_VALUE;
}

/** Broker + manager sessions: board-driven background roles, never a badge target. */
export function isBackgroundRoleSession(labels: Record<string, string> | undefined): boolean {
  const role = labels?.[ROLE_LABEL_KEY];
  return role === BROKER_ROLE_VALUE || role === MANAGER_ROLE_VALUE;
}

function agentRolePath(role: string, suffix: string): string {
  return `/v1/agent-tasks/roles/${encodeURIComponent(role)}/${suffix}`;
}

export interface RoleCandidateAgent {
  id: string;
  name: string;
  /** True for packaged built-ins (importable as a private fork). */
  packaged: boolean;
}

export interface SecretaryProfile {
  role?: string;
  title?: string;
  kind?: string;
  system?: boolean;
  deletable?: boolean;
  /** Null for external roles, which name no Omnigent agent. */
  agent_profile_id: string | null;
  /** Display name of the bound agent profile (resolved server-side). */
  agent_name?: string | null;
  /** Packaged agents backing this role's kind, for the role-form dropdown. */
  candidate_agents?: RoleCandidateAgent[];
  /** The bound backing profile's system prompt (single profile GET only). */
  prompt?: string | null;
  conversation_id: string | null;
  /** Null when the harness resolves its own model (e.g. Codex, OpenCode). */
  model: string | null;
  harness: string | null;
  host_id: string | null;
  workspace: string | null;
  /** What the role specializes in; surfaced to the manager when picking a worker lane. */
  description: string | null;
}

export type RoleProfileSummary = SecretaryProfile & { role: string };

export interface CreateManagerRoleProfileRequest {
  slug: string;
  description?: string | null;
  agent_profile_id?: string;
  harness?: string | null;
  model?: string | null;
  host_id?: string | null;
  workspace?: string | null;
}

export interface UpdateTaskItemRequest {
  title?: string;
  description?: string | null;
  instructions?: string | null;
  internal_note?: string | null;
  worker_id?: string;
  edit_lease_token?: string;
}

export interface UpdateAgentTaskRequest {
  manager_role_key?: string;
  goal?: string;
  description?: string | null;
  priority?: number;
}

export interface SecretarySession {
  role?: string;
  conversation_id: string;
  created: boolean;
}

export function parseEventPayload(
  payload: string | Record<string, unknown> | null | undefined,
): DispatchPayload {
  if (payload == null) return {};
  if (typeof payload === "string") {
    if (!payload.trim()) return {};
    try {
      return JSON.parse(payload) as DispatchPayload;
    } catch {
      return {};
    }
  }
  return payload as DispatchPayload;
}

async function readJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

async function readJsonOrApiError<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { error?: { message?: string } };
      if (body.error?.message) {
        message = body.error.message;
      }
    } catch {
      // Keep the status-line fallback when the body is not JSON.
    }
    throw new Error(message);
  }
  return (await res.json()) as T;
}

export async function fetchAgentTasks(state = "idle"): Promise<AgentTaskSummary[]> {
  const res = await authenticatedFetch(
    `/v1/agent-tasks?state=${encodeURIComponent(state)}&limit=100`,
  );
  const body = await readJson<{ data: AgentTaskSummary[] }>(res);
  return body.data;
}

/** Live managed tasks (active/idle/agent-resolved; excludes archived). */
export async function fetchLiveAgentTasks(): Promise<AgentTaskSummary[]> {
  const [active, idle, agentResolved] = await Promise.all([
    fetchAgentTasks("active"),
    fetchAgentTasks("idle"),
    fetchAgentTasks("agent-resolved"),
  ]);
  return [...active, ...idle, ...agentResolved];
}

export async function fetchTaskDashboard(taskId: string): Promise<TaskDashboard> {
  const res = await authenticatedFetch(`/v1/agent-tasks/${encodeURIComponent(taskId)}/dashboard`);
  return readJson<TaskDashboard>(res);
}

/** Server-side board search match for one task. Ids let the board ring the
 * matched rows without any client-side text matching. */
export interface AgentTaskBoardMatch {
  task_id: string;
  /** Coarse match sources: "task" | "item" | "asset" | "worker". */
  matched_in: string[];
  item_ids: string[];
  asset_ids: number[];
  worker_ids: string[];
}

/** Window size for the server-side board search: matches the server's
 * default limit. When a query returns this many matches, more matches likely
 * exist below the fetched window — the board hints at it instead of
 * paginating (pagination comes later). */
export const AGENT_TASK_BOARD_SEARCH_LIMIT = 100;

export async function fetchAgentTaskBoardSearch(query: string): Promise<AgentTaskBoardMatch[]> {
  const res = await authenticatedFetch(
    `/v1/agent-tasks/board-search?q=${encodeURIComponent(query)}&limit=${AGENT_TASK_BOARD_SEARCH_LIMIT}`,
  );
  const body = await readJson<{ results: AgentTaskBoardMatch[] }>(res);
  return body.results;
}

export interface CreateTaskItemRequest {
  title: string;
  description?: string | null;
  instructions?: string | null;
  worker_id?: string | null;
  state?: string;
  kind?: "work" | "human_action";
  submit_for_user_ack?: boolean;
}

export async function createTaskItem(
  taskId: string,
  body: CreateTaskItemRequest,
): Promise<TaskItemSummary> {
  const res = await authenticatedFetch(`/v1/agent-tasks/${encodeURIComponent(taskId)}/items`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return readJsonOrApiError<TaskItemSummary>(res);
}

export interface WorkerAssignmentInput {
  item_id: string;
  worker_id?: string;
  provider_id?: string;
  host_id?: string;
  workspace?: string;
  edit_lease_token?: string;
}

export async function assignTaskItemWorker(
  taskId: string,
  assignment: WorkerAssignmentInput,
): Promise<void> {
  const res = await authenticatedFetch(
    `/v1/agent-tasks/${encodeURIComponent(taskId)}/workers/assign`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ assignments: [assignment] }),
    },
  );
  await readJsonOrApiError(res);
}

export interface QueueHold {
  token: string;
  expires_at: number;
}

export async function acquireManagerQueueHold(taskId: string, token?: string): Promise<QueueHold> {
  const res = await authenticatedFetch(
    `/v1/agent-tasks/${encodeURIComponent(taskId)}/manager-queue-hold`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    },
  );
  return readJsonOrApiError<QueueHold>(res);
}

export async function releaseManagerQueueHold(taskId: string, token: string): Promise<void> {
  const res = await authenticatedFetch(
    `/v1/agent-tasks/${encodeURIComponent(taskId)}/manager-queue-hold/${encodeURIComponent(token)}`,
    { method: "DELETE" },
  );
  if (!res.ok) await readJsonOrApiError(res);
}

export type ItemEditLease = QueueHold;

export async function acquireTaskItemEditLease(
  itemId: string,
  token?: string,
): Promise<ItemEditLease> {
  const res = await authenticatedFetch(`/v1/task-items/${encodeURIComponent(itemId)}/edit-lease`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  return readJsonOrApiError<ItemEditLease>(res);
}

export async function releaseTaskItemEditLease(itemId: string, token: string): Promise<void> {
  const res = await authenticatedFetch(
    `/v1/task-items/${encodeURIComponent(itemId)}/edit-lease/${encodeURIComponent(token)}`,
    { method: "DELETE" },
  );
  if (!res.ok) await readJsonOrApiError(res);
}

export async function moveTaskToQueueEnd(taskId: string): Promise<void> {
  const res = await authenticatedFetch(
    `/v1/agent-tasks/${encodeURIComponent(taskId)}/move-to-queue-end`,
    { method: "POST" },
  );
  if (!res.ok) await readJsonOrApiError(res);
}

export async function deleteTaskAsset(taskId: string, assetId: number): Promise<void> {
  const res = await authenticatedFetch(
    `/v1/agent-tasks/${encodeURIComponent(taskId)}/assets/${assetId}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
}

export interface WorkerLaneSummary {
  id: string;
  task_id: string;
  kind: string;
  target_id: string | null;
  state: string;
  needs_response: boolean;
  provider_name: string | null;
  failure_reason: string | null;
}

/** Initialize a Worker asynchronously. */
export async function untrackWorker(workerId: string): Promise<void> {
  const res = await authenticatedFetch(`/v1/task-workers/${encodeURIComponent(workerId)}/untrack`, {
    method: "POST",
  });
  if (!res.ok) await readJsonOrApiError(res);
}

export async function reassignWorker(workerId: string, taskId: string): Promise<void> {
  const res = await authenticatedFetch(
    `/v1/task-workers/${encodeURIComponent(workerId)}/reassign`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_id: taskId }),
    },
  );
  if (!res.ok) await readJsonOrApiError(res);
}

export async function initializeWorker(workerId: string): Promise<WorkerLaneSummary> {
  const res = await authenticatedFetch(
    `/v1/task-workers/${encodeURIComponent(workerId)}/initialize`,
    { method: "POST" },
  );
  return readJsonOrApiError<WorkerLaneSummary>(res);
}

export interface UpdateAgentRoleProfileRequest {
  name?: string;
  agent_profile_id?: string;
  harness?: string | null;
  model?: string | null;
  host_id?: string | null;
  workspace?: string | null;
  description?: string | null;
}

export async function fetchRoleProfiles(prefix?: string): Promise<RoleProfileSummary[]> {
  const query = prefix ? `?prefix=${encodeURIComponent(prefix)}` : "";
  const res = await authenticatedFetch(`/v1/agent-tasks/roles/profiles${query}`);
  const body = await readJsonOrApiError<{ data: RoleProfileSummary[] }>(res);
  return body.data;
}

export async function createManagerRoleProfile(
  body: CreateManagerRoleProfileRequest,
): Promise<RoleProfileSummary> {
  const res = await authenticatedFetch("/v1/agent-tasks/roles/manager", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return readJsonOrApiError<RoleProfileSummary>(res);
}

export async function deleteAgentRoleProfile(role: string): Promise<void> {
  const res = await authenticatedFetch(`/v1/agent-tasks/roles/${encodeURIComponent(role)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    await readJsonOrApiError(res);
  }
}

export async function patchAgentTask(
  taskId: string,
  body: UpdateAgentTaskRequest,
): Promise<AgentTaskSummary> {
  const res = await authenticatedFetch(`/v1/agent-tasks/${encodeURIComponent(taskId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return readJsonOrApiError<AgentTaskSummary>(res);
}

export async function archiveAgentTask(taskId: string): Promise<void> {
  const res = await authenticatedFetch(`/v1/agent-tasks/${encodeURIComponent(taskId)}`, {
    method: "DELETE",
  });
  if (!res.ok) await readJsonOrApiError(res);
}

export async function permanentlyDeleteAgentTask(taskId: string): Promise<void> {
  const res = await authenticatedFetch(`/v1/agent-tasks/${encodeURIComponent(taskId)}/permanent`, {
    method: "DELETE",
  });
  if (!res.ok) await readJsonOrApiError(res);
}

export interface SpawnManagerNoticeResult {
  unmanaged_count: number;
  superseded: number;
  event_id: string | null;
}

/** Queue one broker notice asking it to spawn manager(s) for unmanaged tasks. */
export async function spawnManagerNotice(): Promise<SpawnManagerNoticeResult> {
  const res = await authenticatedFetch("/v1/agent-tasks/spawn-manager-notice", {
    method: "POST",
  });
  return readJsonOrApiError<SpawnManagerNoticeResult>(res);
}

export async function fetchAgentRoleProfile(role: string): Promise<SecretaryProfile> {
  const res = await authenticatedFetch(agentRolePath(role, "profile"));
  return readJsonOrApiError<SecretaryProfile>(res);
}

export async function updateAgentRoleProfile(
  role: string,
  body: UpdateAgentRoleProfileRequest,
): Promise<SecretaryProfile> {
  const res = await authenticatedFetch(agentRolePath(role, "profile"), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return readJsonOrApiError<SecretaryProfile>(res);
}

export async function updateRolePrompt(role: string, prompt: string): Promise<SecretaryProfile> {
  const res = await authenticatedFetch(agentRolePath(role, "prompt"), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt }),
  });
  return readJsonOrApiError<SecretaryProfile>(res);
}

export async function ensureAgentRoleSession(role: string): Promise<SecretarySession> {
  const res = await authenticatedFetch(agentRolePath(role, "session"), {
    method: "POST",
  });
  return readJsonOrApiError<SecretarySession>(res);
}

export async function resetAgentRoleSession(role: string): Promise<SecretarySession> {
  const res = await authenticatedFetch(agentRolePath(role, "session/reset"), {
    method: "POST",
  });
  return readJsonOrApiError<SecretarySession>(res);
}

export async function fetchSecretaryProfile(): Promise<SecretaryProfile> {
  return fetchAgentRoleProfile(TASK_SECRETARY_ROLE);
}

export async function fetchBrokerProfile(): Promise<SecretaryProfile> {
  return fetchAgentRoleProfile(TASK_BROKER_ROLE);
}

export async function ensureSecretarySession(): Promise<SecretarySession> {
  return ensureAgentRoleSession(TASK_SECRETARY_ROLE);
}

export async function ensureBrokerSession(): Promise<SecretarySession> {
  return ensureAgentRoleSession(TASK_BROKER_ROLE);
}

export async function resetSecretarySession(): Promise<SecretarySession> {
  return resetAgentRoleSession(TASK_SECRETARY_ROLE);
}

export async function resetBrokerSession(): Promise<SecretarySession> {
  return resetAgentRoleSession(TASK_BROKER_ROLE);
}

export async function fireTaskItem(
  taskItemId: string,
  editedPayload?: DispatchPayload,
): Promise<void> {
  const res = await authenticatedFetch(`/v1/task-items/${encodeURIComponent(taskItemId)}/fire`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ edited_payload: editedPayload ?? null }),
  });
  if (!res.ok) await readJsonOrApiError(res);
}

export async function closeTaskItem(taskItemId: string): Promise<void> {
  const res = await authenticatedFetch(`/v1/task-items/${encodeURIComponent(taskItemId)}/close`, {
    method: "POST",
  });
  if (!res.ok) await readJsonOrApiError(res);
}

export async function updateTaskItem(
  taskItemId: string,
  body: UpdateTaskItemRequest,
): Promise<TaskItemSummary> {
  const res = await authenticatedFetch(`/v1/task-items/${encodeURIComponent(taskItemId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return readJson<TaskItemSummary>(res);
}

export async function cancelTaskItem(taskItemId: string): Promise<void> {
  const res = await authenticatedFetch(`/v1/task-items/${encodeURIComponent(taskItemId)}/cancel`, {
    method: "POST",
  });
  if (!res.ok) await readJsonOrApiError(res);
}

export async function cancelAgentQueueItem(queueItemId: string): Promise<void> {
  const res = await authenticatedFetch(
    `/v1/agent-queue-items/${encodeURIComponent(queueItemId)}/cancel`,
    { method: "POST" },
  );
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
}

export async function interruptAgentQueueItem(queueItemId: string): Promise<void> {
  const res = await authenticatedFetch(
    `/v1/agent-queue-items/${encodeURIComponent(queueItemId)}/interrupt`,
    { method: "POST" },
  );
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
}

export async function retryTaskItemDispatch(taskItemId: string): Promise<void> {
  const res = await authenticatedFetch(
    `/v1/task-items/${encodeURIComponent(taskItemId)}/retry-dispatch`,
    { method: "POST" },
  );
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
}

export interface FyiClusterCard {
  id: string;
  kind: "fyi_cluster";
  state: "pending";
  created_at: number;
  resolved_at: number | null;
  headline: string;
  rationale: string | null;
  body: {
    events: TaskEventSummary[];
  };
}

export interface BoardTriage {
  fyi: FyiClusterCard[];
}

export type FyiResolution = "dismiss_fyi";

export async function fetchBoardTriage(): Promise<BoardTriage> {
  const res = await authenticatedFetch("/v1/agent-tasks/board/pending");
  return readJson<BoardTriage>(res);
}

export async function resolveFyiCluster(
  clusterId: string,
  body: {
    resolution: FyiResolution;
  },
): Promise<void> {
  const res = await authenticatedFetch(
    `/v1/fyi-clusters/${encodeURIComponent(clusterId)}/resolve`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
}

export type ScriptPluginKind = "poll";

export interface ScriptPluginHealthRow {
  host_id: string;
  name: string;
  kind: ScriptPluginKind;
  outcome: string;
  enabled: boolean;
  builtin?: boolean;
  last_run_at: number | null;
  last_success_at: number | null;
  last_failure_at: number | null;
  last_error: string | null;
  consecutive_failures: number;
  singleton_skipped: boolean;
  warning: string | null;
  interval_s: number | null;
  updated_at: number;
}

export async function fetchScriptPluginHealth(
  kind?: ScriptPluginKind,
): Promise<ScriptPluginHealthRow[]> {
  const qs = kind ? `?kind=${encodeURIComponent(kind)}` : "";
  const res = await authenticatedFetch(`/v1/agent-tasks/script-plugins/health${qs}`);
  const body = await readJson<{ plugins: ScriptPluginHealthRow[] }>(res);
  return body.plugins;
}

export async function updateScriptPollPlugin(
  hostId: string,
  name: string,
  enabled: boolean,
): Promise<void> {
  const res = await authenticatedFetch(
    `/v1/agent-tasks/script-plugins/hosts/${encodeURIComponent(hostId)}/${encodeURIComponent(name)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    },
  );
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail ?? `${res.status} ${res.statusText}`);
  }
}

/** Stop keys the dispatcher currently refuses to dispatch.
 *
 * Keys are bare roles ("broker") or scope-qualified ("manager:<id>") for a
 * single manager's queue.
 */
export async function fetchDispatchStoplist(): Promise<string[]> {
  const res = await authenticatedFetch("/v1/agent-queues/dispatch-stoplist");
  const body = await readJson<{ data: string[] }>(res);
  return body.data;
}

export async function setRoleDispatchStopped(
  role: string,
  stopped: boolean,
  scopeId?: string | null,
): Promise<void> {
  const res = await authenticatedFetch("/v1/agent-queues/dispatch-stoplist", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role, stopped, scope_id: scopeId ?? null }),
  });
  if (!res.ok) await readJsonOrApiError(res);
}

export interface TaskManagerSummary {
  id: string;
  conversation_id: string | null;
  title: string;
  role_key: string;
  task_count: number;
}

/** The caller's registered first-class managers. */
export async function fetchManagers(): Promise<TaskManagerSummary[]> {
  const res = await authenticatedFetch("/v1/agent-tasks/managers");
  const body = await readJson<{ managers: TaskManagerSummary[] }>(res);
  return body.managers;
}

/** Delete a manager: its session conversation, its queued work, and its row. */
export async function deleteManager(managerId: string): Promise<void> {
  const res = await authenticatedFetch(
    `/v1/agent-tasks/managers/${encodeURIComponent(managerId)}`,
    {
      method: "DELETE",
    },
  );
  if (!res.ok) await readJsonOrApiError(res);
}

export interface QueueEventBacklogRow {
  role: string;
  scope_id: string | null;
  count: number;
}

export interface EventBacklog {
  data: QueueEventBacklogRow[];
  /** Non-dispatched in-flight states (transient ingress, FYI bucket). */
  other: Record<string, number>;
}

/** Waiting event counts per dispatch queue (broker + per manager). */
export async function fetchEventBacklog(): Promise<EventBacklog> {
  const res = await authenticatedFetch("/v1/agent-queues/event-backlog");
  return readJson<EventBacklog>(res);
}

/** Dismiss every event waiting on one dispatch queue. */
export async function dismissQueueBacklog(
  role: string,
  scopeId?: string | null,
): Promise<{ dismissed: number; cancelled_items: number }> {
  const res = await authenticatedFetch("/v1/agent-queues/dismiss-backlog", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role, scope_id: scopeId ?? null }),
  });
  return readJsonOrApiError(res);
}
