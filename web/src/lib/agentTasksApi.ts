import { authenticatedFetch } from "@/lib/identity";

export interface AgentTaskSummary {
  id: string;
  title: string;
  description: string | null;
  state: string;
  manager_role_key: string;
  worker_role_key: string;
  manager_conversation_id: string | null;
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
  /** The worker role this lane runs. Null for externally adopted lanes. */
  role_key: string | null;
  /** Set only for externally adopted lanes, which have no role. */
  agent_profile_id: string | null;
  kind: string;
  session_id: string | null;
  state: TaskWorkerLaneState;
  situation: string;
  rows: TaskWorkerRow[];
  executions: TaskExecutionSummary[];
}

export interface TaskAssetSummary {
  id: number;
  kind: "url";
  title: string;
  url: string | null;
  created_at: number;
}

export interface TaskDashboard {
  task: {
    id: string;
    title: string;
    description: string | null;
    state: string;
    manager_conversation_id: string | null;
  };
  derived: {
    has_running_workers: boolean;
  };
  inbox_items: TaskItemSummary[];
  reconcile_queue_count: number;
  assets: TaskAssetSummary[];
  workers: TaskWorkerLane[];
}

export interface DispatchPayload {
  worker_role_key?: string;
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
export const WORKER_DEFAULT_ROLE_KEY = "worker:default";
export const WORKER_ROLE_PREFIX = "worker:";

// Conversation label marking a PuppyGarden role session. Mirrors the backend
// ``omnigent.agent_tasks.session_labels`` constants. ``task_broker`` is a
// background agent whose chat is not a reading surface, so the sidebar never
// shows its unread dot and excludes it from the unread badge count.
export const ROLE_LABEL_KEY = "omnigent.role";
export const BROKER_ROLE_VALUE = "task_broker";
export const SECRETARY_ROLE_VALUE = "task_secretary";

export function isBrokerSession(labels: Record<string, string> | undefined): boolean {
  return labels?.[ROLE_LABEL_KEY] === BROKER_ROLE_VALUE;
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
  agent_profile_id?: string;
  harness?: string | null;
  model?: string | null;
  host_id?: string | null;
  workspace?: string | null;
}

export type CreateWorkerRoleProfileRequest = CreateManagerRoleProfileRequest;

export interface UpdateAgentTaskRequest {
  manager_role_key?: string;
  worker_role_key?: string;
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

/** Active and idle managed tasks (excludes pending packages and archived). */
export async function fetchLiveAgentTasks(): Promise<AgentTaskSummary[]> {
  const [active, idle] = await Promise.all([fetchAgentTasks("active"), fetchAgentTasks("idle")]);
  return [...active, ...idle];
}

export async function fetchTaskDashboard(taskId: string): Promise<TaskDashboard> {
  const res = await authenticatedFetch(`/v1/agent-tasks/${encodeURIComponent(taskId)}/dashboard`);
  return readJson<TaskDashboard>(res);
}

export interface WorkerLaneSummary {
  id: string;
  task_id: string;
  kind: string;
  role_key: string | null;
  agent_profile_id: string | null;
  session_id: string | null;
}

/** Re-point a worker lane at another worker role. Only valid before it has a session. */
export async function updateWorkerLaneRole(
  workerId: string,
  roleKey: string,
): Promise<WorkerLaneSummary> {
  const res = await authenticatedFetch(`/v1/task-workers/${encodeURIComponent(workerId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role_key: roleKey }),
  });
  return readJsonOrApiError<WorkerLaneSummary>(res);
}

/** Start a worker sub-agent session for a lane that has not run yet. */
export async function activateWorkerLane(workerId: string): Promise<WorkerLaneSummary> {
  const res = await authenticatedFetch(
    `/v1/task-workers/${encodeURIComponent(workerId)}/activate`,
    { method: "POST" },
  );
  return readJsonOrApiError<WorkerLaneSummary>(res);
}

export interface UpdateAgentRoleProfileRequest {
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

export async function createWorkerRoleProfile(
  body: CreateWorkerRoleProfileRequest,
): Promise<RoleProfileSummary> {
  const res = await authenticatedFetch("/v1/agent-tasks/roles/worker", {
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

export async function acceptAgentTaskPackage(taskId: string): Promise<AgentTaskSummary> {
  const res = await authenticatedFetch(
    `/v1/agent-tasks/${encodeURIComponent(taskId)}/accept-package`,
    { method: "POST" },
  );
  return readJsonOrApiError<AgentTaskSummary>(res);
}

export async function rejectAgentTaskPackage(taskId: string): Promise<AgentTaskSummary> {
  const res = await authenticatedFetch(
    `/v1/agent-tasks/${encodeURIComponent(taskId)}/reject-package`,
    { method: "POST" },
  );
  return readJsonOrApiError<AgentTaskSummary>(res);
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

export async function importRoleAgent(role: string, agentId: string): Promise<SecretaryProfile> {
  const res = await authenticatedFetch(agentRolePath(role, "import-agent"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agent_id: agentId }),
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

export type ItemResolution = "accept_item" | "edit_and_dispatch" | "reject_item";

export async function resolveTaskItem(
  taskItemId: string,
  body: {
    resolution: ItemResolution;
    edited_payload?: DispatchPayload;
  },
): Promise<void> {
  const res = await authenticatedFetch(`/v1/task-items/${encodeURIComponent(taskItemId)}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
}

export async function updateTaskItem(
  taskItemId: string,
  body: DispatchPayload & { title?: string; instructions?: string },
): Promise<TaskItemSummary> {
  const res = await authenticatedFetch(`/v1/task-items/${encodeURIComponent(taskItemId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return readJson<TaskItemSummary>(res);
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

export type FyiResolution = "dismiss_fyi" | "promote_to_routing";

export async function fetchBoardTriage(): Promise<BoardTriage> {
  const res = await authenticatedFetch("/v1/agent-tasks/board/pending");
  return readJson<BoardTriage>(res);
}

export async function resolveFyiCluster(
  clusterId: string,
  body: {
    resolution: FyiResolution;
    routing_title?: string;
    routing_instructions?: string;
    suggested_task_id?: string | null;
    proposed_task_title?: string;
    proposed_task_internal_note?: string;
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

export type ScriptPluginKind = "poll" | "timer";

export interface ScriptPluginHealthRow {
  host_id: string;
  name: string;
  kind: ScriptPluginKind;
  outcome: string;
  last_run_at: number | null;
  last_success_at: number | null;
  last_failure_at: number | null;
  last_error: string | null;
  consecutive_failures: number;
  singleton_skipped: boolean;
  interval_s: number | null;
  fire_at: number | null;
  fired_at: number | null;
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
