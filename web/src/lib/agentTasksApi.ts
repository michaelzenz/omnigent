import { authenticatedFetch } from "@/lib/identity";

export interface AgentTaskSummary {
  id: string;
  title: string;
  description: string | null;
  state: string;
  agent_profile_id: string;
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
  created_at: number;
  updated_at: number | null;
}

export interface TaskExecutionSummary {
  id: string;
  task_item_id: string;
  event_id: string;
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
  profile_id: string;
  session_id: string | null;
  state: TaskWorkerLaneState;
  situation: string;
  rows: TaskWorkerRow[];
  executions: TaskExecutionSummary[];
}

/** @deprecated Use TaskWorkerLane */
export interface TaskWorkerGroup {
  profile_id: string;
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
  worker_profile_id?: string;
  title?: string;
  instructions?: string;
  host_id?: string;
  workspace?: string;
  harness?: string;
  model?: string;
}

export interface SecretaryProfile {
  agent_id: string;
  conversation_id: string | null;
  model: string;
  harness: string;
  host_id: string;
  workspace: string;
}

export interface SecretarySession {
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
  const [active, idle] = await Promise.all([
    fetchAgentTasks("active"),
    fetchAgentTasks("idle"),
  ]);
  return [...active, ...idle];
}

export async function fetchTaskDashboard(taskId: string): Promise<TaskDashboard> {
  const res = await authenticatedFetch(`/v1/agent-tasks/${encodeURIComponent(taskId)}/dashboard`);
  return readJson<TaskDashboard>(res);
}

export async function fetchSecretaryProfile(): Promise<SecretaryProfile> {
  const res = await authenticatedFetch("/v1/agent-tasks/secretary/profile");
  return readJsonOrApiError<SecretaryProfile>(res);
}

export async function ensureSecretarySession(): Promise<SecretarySession> {
  const res = await authenticatedFetch("/v1/agent-tasks/secretary/session", {
    method: "POST",
  });
  return readJsonOrApiError<SecretarySession>(res);
}

export async function resetSecretarySession(): Promise<SecretarySession> {
  const res = await authenticatedFetch("/v1/agent-tasks/secretary/session/reset", {
    method: "POST",
  });
  return readJsonOrApiError<SecretarySession>(res);
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
