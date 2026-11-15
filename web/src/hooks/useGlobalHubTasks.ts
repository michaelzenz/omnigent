import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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

/** The session a worker wraps, with the title and live state the sidebar shows. */
export interface AgentTaskWorkerSession {
  title: string | null;
  status: "idle" | "running" | "failed";
  pending_elicitations_count: number;
  background_work_running: boolean;
  blocked_on: string | null;
  /** Unix epoch seconds of the session's last change. */
  updated_at: number;
}

/** A session working on a task. */
export interface AgentTaskWorker {
  id: string;
  task_id: string;
  session_id: string;
  /** The manager's short label for the session's recent work on the task. */
  title: string | null;
  created_at: number;
  updated_at: number | null;
  session: AgentTaskWorkerSession;
}

/** A link a task carries, such as a design doc or a pull request. */
export interface AgentTaskAsset {
  id: string;
  task_id: string;
  /** Free-form label that groups the task's assets, e.g. ``"documents"``. */
  category: string;
  title: string;
  url: string;
  /** The sessions that produced or used the link. */
  related_session_ids: string[];
  created_at: number;
  updated_at: number | null;
}

/** A live task with the workers and assets its card shows; busy workers come first. */
export interface AgentTaskCard extends AgentTask {
  workers: AgentTaskWorker[];
  assets: AgentTaskAsset[];
}

/** The first live tasks on the user's board, in board order. */
export interface AgentTaskBoard {
  data: AgentTaskCard[];
  /** Whether the board has more live tasks than ``data`` holds. */
  has_more: boolean;
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

/** The cards the board reads first, and how many more each scroll to its end reads. */
export const BOARD_WINDOW = 20;
/** The most cards one board read returns. */
export const BOARD_MAX_CARDS = 1000;
// The broker and the managers change the board in the background.
const BOARD_REFRESH_MS = 10_000;

async function readJson<T>(res: Response): Promise<T> {
  if (!res.ok) throw await apiErrorFromResponse(res);
  return (await res.json()) as T;
}

async function fetchBoard(limit: number): Promise<AgentTaskBoard> {
  return readJson(await authenticatedFetch(`/v1/agent-tasks/board?limit=${limit}`));
}

async function fetchManagers(): Promise<AgentManager[]> {
  const res = await authenticatedFetch("/v1/agent-tasks/managers");
  return (await readJson<{ data: AgentManager[] }>(res)).data;
}

/** The first ``limit`` live tasks (active, idle or resolved) as cards, in board order. */
export function useGlobalHubBoard(limit: number) {
  return useQuery({
    queryKey: [...GLOBALHUB_TASKS_QUERY_KEY, "board", limit],
    queryFn: () => fetchBoard(limit),
    refetchInterval: BOARD_REFRESH_MS,
    // The loaded cards stay on screen while a longer window loads.
    placeholderData: keepPreviousData,
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

/** Take a session off its task; the session keeps running. */
export function useUntrackWorker() {
  return useTaskAction((workerId: string) =>
    sendTaskRequest(`/v1/task-workers/${encodeURIComponent(workerId)}/untrack`, {
      method: "POST",
    }),
  );
}

/** Remove a link from a task. */
export function useDeleteTaskAsset(taskId: string) {
  return useTaskAction((assetId: string) =>
    sendTaskRequest(`${taskPath(taskId)}/assets/${encodeURIComponent(assetId)}`, {
      method: "DELETE",
    }),
  );
}
