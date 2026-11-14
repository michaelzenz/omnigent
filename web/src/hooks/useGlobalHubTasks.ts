import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authenticatedFetch } from "@/lib/identity";
import { apiErrorFromResponse } from "@/lib/sessionsApi";

export type AgentTaskState = "active" | "idle" | "agent-resolved" | "archived";

/** A task on the user's Global Hub board. */
export interface AgentTask {
  id: string;
  manager_id: string | null;
  title: string;
  goal: string;
  description: string | null;
  state: AgentTaskState;
  /** 0 is the highest priority, 3 the lowest. */
  priority: number;
  /** Board order: higher ranks list first. */
  queue_rank: number;
  /** Unix epoch seconds. */
  created_at: number;
  updated_at: number | null;
}

/** A manager on the user's board, with the fields a task card reads. */
export interface AgentManager {
  id: string;
  title: string;
  conversation_id: string | null;
  session_missing: boolean;
}

/** Fields a card can change on a task. */
export interface AgentTaskChanges {
  goal?: string;
  state?: AgentTaskState;
}

export const GLOBALHUB_TASKS_QUERY_KEY = ["globalhub-tasks"] as const;
export const GLOBALHUB_MANAGERS_QUERY_KEY = ["globalhub-managers"] as const;

const LIVE_STATES: readonly AgentTaskState[] = ["active", "idle", "agent-resolved"];
// The most tasks the list route returns in one call.
const TASKS_PER_STATE = 100;
// The broker and the managers change the board in the background.
const BOARD_REFRESH_MS = 10_000;

async function readJson<T>(res: Response): Promise<T> {
  if (!res.ok) throw await apiErrorFromResponse(res);
  return (await res.json()) as T;
}

async function fetchTasks(state: AgentTaskState): Promise<AgentTask[]> {
  const res = await authenticatedFetch(`/v1/agent-tasks?state=${state}&limit=${TASKS_PER_STATE}`);
  return (await readJson<{ data: AgentTask[] }>(res)).data;
}

async function fetchLiveTasks(): Promise<AgentTask[]> {
  const lists = await Promise.all(LIVE_STATES.map(fetchTasks));
  return lists.flat().sort((a, b) => b.queue_rank - a.queue_rank);
}

async function fetchManagers(): Promise<AgentManager[]> {
  const res = await authenticatedFetch("/v1/agent-tasks/managers");
  return (await readJson<{ data: AgentManager[] }>(res)).data;
}

/** Every live task (active, idle or resolved), in board order. */
export function useLiveAgentTasks() {
  return useQuery({
    queryKey: [...GLOBALHUB_TASKS_QUERY_KEY, "live"],
    queryFn: fetchLiveTasks,
    refetchInterval: BOARD_REFRESH_MS,
  });
}

/** The user's managers, for the names on the task cards. */
export function useAgentManagers() {
  return useQuery({
    queryKey: GLOBALHUB_MANAGERS_QUERY_KEY,
    queryFn: fetchManagers,
    refetchInterval: BOARD_REFRESH_MS,
  });
}

function taskPath(taskId: string): string {
  return `/v1/agent-tasks/${encodeURIComponent(taskId)}`;
}

async function sendTaskRequest(path: string, init: RequestInit): Promise<void> {
  const res = await authenticatedFetch(path, init);
  if (!res.ok) throw await apiErrorFromResponse(res);
}

/** A task action that refreshes the board once it ends, whether it worked or not. */
function useTaskAction<T = void>(action: (variables: T) => Promise<void>) {
  const queryClient = useQueryClient();
  return useMutation<void, Error, T>({
    mutationFn: action,
    onSettled: () => queryClient.invalidateQueries({ queryKey: GLOBALHUB_TASKS_QUERY_KEY }),
  });
}

/** Change a task's goal or state; ``agent-resolved`` also moves it to the end. */
export function usePatchAgentTask(taskId: string) {
  return useTaskAction((changes: AgentTaskChanges) =>
    sendTaskRequest(taskPath(taskId), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(changes),
    }),
  );
}

/** Move a task below the other open tasks, or to the very end once resolved. */
export function useMoveTaskToQueueEnd(taskId: string) {
  return useTaskAction(() =>
    sendTaskRequest(`${taskPath(taskId)}/move-to-queue-end`, { method: "POST" }),
  );
}

/** Take a task off the board and keep its data. */
export function useArchiveAgentTask(taskId: string) {
  return useTaskAction(() => sendTaskRequest(taskPath(taskId), { method: "DELETE" }));
}

/** Remove a task for good, with its workers and links. */
export function useDeleteAgentTaskPermanently(taskId: string) {
  return useTaskAction(() =>
    sendTaskRequest(`${taskPath(taskId)}/permanent`, { method: "DELETE" }),
  );
}
