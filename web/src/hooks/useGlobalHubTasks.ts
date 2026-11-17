import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authenticatedFetch } from "@/lib/identity";
import { apiErrorFromResponse } from "@/lib/sessionsApi";

export type AgentTaskState = "active" | "idle" | "agent-resolved" | "archived";

/** A task on the user's Global Hub board. */
export interface AgentTask {
  id: string;
  manager_id: string | null;
  title: string;
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

/** A worker's or a role's session, with the title and live state the sidebar shows. */
export interface BoardSession {
  title: string | null;
  status: "idle" | "running" | "failed";
  pending_elicitations_count: number;
  background_work_running: boolean;
  blocked_on: string | null;
  /** Unix epoch seconds of the session's last change. */
  updated_at: number;
  /** When the user last saw the session, as the session list sends it; ``null`` when never. */
  viewer_last_seen?: number | null;
  /** Whether the user marked the session unread. */
  viewer_unread?: boolean;
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
  session: BoardSession;
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

/** How a card matched a board search. */
export interface BoardSearchMatch {
  /** How many distinct query words the card holds. */
  hits: number;
  asset_ids: string[];
  worker_ids: string[];
}

/** A live task with the workers and assets its card shows; busy workers come first. */
export interface AgentTaskCard extends AgentTask {
  workers: AgentTaskWorker[];
  assets: AgentTaskAsset[];
  /** How the card matched, while the board shows a search. */
  match?: BoardSearchMatch | null;
}

/** The first live tasks on the user's board, in board order. */
export interface AgentTaskBoard {
  data: AgentTaskCard[];
  /** Whether the board has more live tasks than ``data`` holds. */
  has_more: boolean;
  /** How many live tasks on the whole board have no manager. */
  unmanaged_count: number;
}

/** A request for managers; ``queued`` is false when every live task already had one. */
export interface SpawnManagersRequested {
  unmanaged_count: number;
  queued: boolean;
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
  state?: AgentTaskState;
  /** 0 (P0, most urgent) to 3 (P3). */
  priority?: number;
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

/** The distinct lowercase words of a board search, as the server reads them. */
export function boardSearchWords(query: string): string[] {
  return [...new Set(query.toLowerCase().split(/\s+/).filter(Boolean))];
}

async function fetchBoard(limit: number, query: string): Promise<AgentTaskBoard> {
  const search = query ? `&q=${encodeURIComponent(query)}` : "";
  return readJson(await authenticatedFetch(`/v1/agent-tasks/board?limit=${limit}${search}`));
}

async function fetchManagers(after: string | null = null): Promise<AgentManager[]> {
  // The board shows full managers too; the broker's list leaves them out.
  const query = new URLSearchParams({ include_full: "true", limit: "200" });
  if (after) query.set("after", after);
  const page = await readJson<{ data: AgentManager[]; has_more?: boolean; last_id?: string }>(
    await authenticatedFetch(`/v1/agent-tasks/managers?${query}`),
  );
  // A server without pages answers once, without has_more.
  if (!page.has_more || !page.last_id) return page.data;
  return [...page.data, ...(await fetchManagers(page.last_id))];
}

/**
 * The first ``limit`` live tasks (active, idle or resolved) as cards, in board
 * order; with a ``query``, the tasks that match it, best match first.
 */
export function useGlobalHubBoard(limit: number, query = "") {
  return useQuery({
    queryKey: [...GLOBALHUB_TASKS_QUERY_KEY, "board", limit, query],
    queryFn: () => fetchBoard(limit, query),
    refetchInterval: BOARD_REFRESH_MS,
    // The loaded cards stay on screen while a longer window loads.
    placeholderData: keepPreviousData,
  });
}

/** The user's managers, for the names on the task cards. */
export function useAgentManagers() {
  return useQuery({
    queryKey: GLOBALHUB_MANAGERS_QUERY_KEY,
    queryFn: () => fetchManagers(),
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

/** Change a task's state or priority; ``agent-resolved`` also moves it to the end. */
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

/** Remove a task for good, with its workers and links. */
export function useDeleteAgentTaskPermanently(taskId: string) {
  return useTaskAction(() =>
    sendTaskRequest(`${taskPath(taskId)}/permanent`, { method: "DELETE" }),
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

/** Ask the broker to give a manager to each live task that has none. */
export function useRequestManagers() {
  const queryClient = useQueryClient();
  return useMutation<SpawnManagersRequested, Error>({
    mutationFn: async () =>
      readJson(await authenticatedFetch("/v1/agent-tasks/spawn-managers", { method: "POST" })),
    onSettled: () => queryClient.invalidateQueries({ queryKey: GLOBALHUB_TASKS_QUERY_KEY }),
  });
}
