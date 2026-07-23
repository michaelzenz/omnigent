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

export interface TaskExecutionSummary {
  id: string;
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
  pending_proposals: TaskEventSummary[];
  pending_inbound_events: TaskEventSummary[];
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

export type ProposalResolution = "accept_proposal" | "edit_and_dispatch" | "reject_proposal";

export async function resolveTaskEvent(
  eventId: string,
  body: {
    resolution: ProposalResolution;
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
