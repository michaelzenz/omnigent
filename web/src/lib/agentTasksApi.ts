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
  model: string;
  harness: string;
  host_id: string;
  workspace: string;
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

export async function fetchSecretaryProfile(): Promise<SecretaryProfile | null> {
  const res = await authenticatedFetch("/v1/agent-tasks/secretary/profile");
  if (res.status === 404) return null;
  return readJson<SecretaryProfile>(res);
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
