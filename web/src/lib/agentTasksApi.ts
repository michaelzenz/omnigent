import { authenticatedFetch } from "@/lib/identity";

export interface AgentTaskSummary {
  id: string;
  title: string;
  description: string | null;
  state: string;
  manager_agent_id: string;
  manager_conversation_id: string | null;
}

export interface TaskEventSummary {
  id: string;
  event_type: string;
  title: string;
  summary: string | null;
  state: string;
  payload: string | Record<string, unknown> | null;
  created_at: number;
  updated_at: number | null;
}

export interface TaskItemSummary {
  id: string;
  title: string;
  instructions: string | null;
  state: string;
  worker_agent_id: string | null;
  model: string | null;
  host_id: string | null;
  workspace: string | null;
  harness: string | null;
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

export interface TaskWorkerGroup {
  worker_agent_id: string;
  executions: TaskExecutionSummary[];
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
  workers: TaskWorkerGroup[];
}

export interface DispatchPayload {
  worker_agent_id?: string;
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

export async function fetchAgentTasks(state = "active"): Promise<AgentTaskSummary[]> {
  const res = await authenticatedFetch(
    `/v1/agent-tasks?state=${encodeURIComponent(state)}&limit=100`,
  );
  const body = await readJson<{ data: AgentTaskSummary[] }>(res);
  return body.data;
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

export type EventResolution = "route_to_task" | "select_attempt";

export async function resolveTaskEvent(
  eventId: string,
  body: {
    resolution: EventResolution;
    task_id?: string;
    routing_attempt_id?: string;
    host_id?: string;
    workspace?: string;
    harness?: string;
    model?: string;
    edited_payload?: DispatchPayload;
  },
): Promise<void> {
  const res = await authenticatedFetch(`/v1/task-events/${encodeURIComponent(eventId)}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
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

export interface RoutingCandidateSummary {
  task_id: string;
  task_title: string;
  score: number | null;
  recommended: boolean;
}

export interface TaskItemRoutingBody {
  title: string;
  instructions: string | null;
  canonical_key: string | null;
  recommended_task_id: string;
  events: TaskEventSummary[];
  candidates: RoutingCandidateSummary[];
  worker_agent_id: string | null;
  model: string | null;
  harness: string | null;
  host_id: string | null;
  workspace: string | null;
}

export interface BoardDecisionCard {
  id: string;
  kind: "task_item_routing";
  state: "pending";
  created_at: number;
  resolved_at: number | null;
  headline: string;
  rationale: string | null;
  body: TaskItemRoutingBody;
}

export type RoutingResolution = "accept_routing" | "reject_routing";

export async function fetchBoardDecisions(): Promise<BoardDecisionCard[]> {
  const res = await authenticatedFetch("/v1/agent-tasks/board/decisions");
  const body = await readJson<{ data: BoardDecisionCard[] }>(res);
  return body.data;
}

export async function resolveRoutingProposal(
  itemId: string,
  body: {
    resolution: RoutingResolution;
    selected_task_id?: string;
    instructions?: string;
  },
): Promise<void> {
  const res = await authenticatedFetch(
    `/v1/task-items/${encodeURIComponent(itemId)}/resolve-routing`,
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
