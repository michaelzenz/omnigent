import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
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
  /** Whether the manager flagged news on the task; a click on its card clears it. */
  has_update?: boolean;
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

/** The session of a worker on one of the user's live tasks. */
export interface BoardWorkerSession {
  session_id: string;
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
  /** Workers whose label holds a query word, or whose session's recent turns are near the query. */
  worker_ids: string[];
  /** Whether the task's own title and overview are near the query in meaning. */
  title_match?: boolean;
}

/** A live task with the workers and assets its card shows; busy workers come first. */
export interface AgentTaskCard extends AgentTask {
  workers: AgentTaskWorker[];
  assets: AgentTaskAsset[];
  /** How the card matched, while the board shows a search. */
  match?: BoardSearchMatch | null;
}

/** One page of the live tasks on the user's board, in board order. */
export interface AgentTaskBoard {
  data: AgentTaskCard[];
  /** Whether more pages follow. */
  has_more: boolean;
  /** Reads the next page; ``null`` on the last page. */
  next_cursor?: string | null;
  /** Reads the page before; ``null`` on the first page. */
  prev_cursor?: string | null;
  /** How many live tasks on the whole board have no manager. */
  unmanaged_count: number;
  /** How many active and idle tasks on the whole board have each priority, P0 first. */
  priority_counts?: number[];
  /** How many live tasks, agent-resolved ones included, have news at each priority, P0 first. */
  priority_update_counts?: number[];
}

/** The active and idle tasks at each priority, P0 first, and the live tasks with news. */
export interface BoardPriorityCounts {
  tasks: readonly number[];
  updates: readonly number[];
}

/** A board page read: after a page's ``next_cursor``, before its ``prev_cursor``, or the first page. */
export type BoardPageParam = { after: string } | { before: string } | null;

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
  /** ``false`` clears the manager's flag of news on the task. */
  has_update?: boolean;
}

export const GLOBALHUB_TASKS_QUERY_KEY = ["globalhub-tasks"] as const;
export const GLOBALHUB_MANAGERS_QUERY_KEY = ["globalhub-managers"] as const;

/** The cards in one board page: the board reads one page first, and one more per scroll to its end. */
export const BOARD_PAGE_SIZE = 20;
/** The most pages the board holds; a page read past them drops the page at the other end. */
export const BOARD_MAX_PAGES = 5;
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

async function fetchBoard(
  page: BoardPageParam,
  query: string,
  priority: number | null,
): Promise<AgentTaskBoard> {
  const params = new URLSearchParams({ limit: String(BOARD_PAGE_SIZE) });
  if (query) params.set("q", query);
  if (priority !== null) params.set("priority", String(priority));
  if (page && "after" in page) params.set("after", page.after);
  if (page && "before" in page) params.set("before", page.before);
  return readJson(await authenticatedFetch(`/v1/agent-tasks/board?${params}`));
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
 * The live tasks (active, idle or resolved) as cards, in board order, a page at
 * a time; with a ``query``, the tasks that match it, best match first. A
 * ``priority`` from 0 to 3 keeps only the tasks of that priority. The
 * board holds at most ``BOARD_MAX_PAGES`` pages, and reads a dropped page
 * again when the user scrolls back to it. A refresh reads the held pages
 * again, from the first. The pages drop when the board closes, so it opens at
 * its top.
 */
export function useGlobalHubBoard(query = "", priority: number | null = null) {
  return useInfiniteQuery({
    queryKey: [...GLOBALHUB_TASKS_QUERY_KEY, "board", query, priority],
    queryFn: ({ pageParam }) => fetchBoard(pageParam, query, priority),
    initialPageParam: null as BoardPageParam,
    getNextPageParam: (page): BoardPageParam | undefined =>
      page.next_cursor ? { after: page.next_cursor } : undefined,
    getPreviousPageParam: (page): BoardPageParam | undefined =>
      page.prev_cursor ? { before: page.prev_cursor } : undefined,
    maxPages: BOARD_MAX_PAGES,
    gcTime: 0,
    refetchInterval: BOARD_REFRESH_MS,
    // A new search keeps the cards on screen until its first page loads.
    placeholderData: keepPreviousData,
  });
}

/**
 * How many active and idle tasks on the whole board have each priority, and how
 * many live tasks have news, from the board read that the cards use;
 * ``undefined`` until that read loads.
 */
export function useBoardPriorityCounts(
  query = "",
  priority: number | null = null,
): BoardPriorityCounts | undefined {
  const page = useGlobalHubBoard(query, priority).data?.pages[0];
  if (page?.priority_counts === undefined) return undefined;
  return { tasks: page.priority_counts, updates: page.priority_update_counts ?? [] };
}

/**
 * Whether any live task on the user's board has news (the "Has update" badge),
 * for the dot on the Global Hub link. A one-card read carries the counts of the
 * whole board.
 */
export function useBoardHasUpdates(): boolean {
  const { data } = useQuery({
    queryKey: [...GLOBALHUB_TASKS_QUERY_KEY, "board-updates"],
    queryFn: async () =>
      (await readJson<AgentTaskBoard>(await authenticatedFetch("/v1/agent-tasks/board?limit=1")))
        .priority_update_counts ?? [],
    refetchInterval: BOARD_REFRESH_MS,
    retry: false,
  });
  return (data ?? []).some((count) => count > 0);
}

/** The user's managers, for the names on the task cards. */
export function useAgentManagers() {
  return useQuery({
    queryKey: GLOBALHUB_MANAGERS_QUERY_KEY,
    queryFn: () => fetchManagers(),
    refetchInterval: BOARD_REFRESH_MS,
  });
}

/** The sessions of the workers on the user's live tasks, each once, for the Global Hub link's dot. */
export function useBoardWorkerSessions() {
  return useQuery({
    queryKey: [...GLOBALHUB_TASKS_QUERY_KEY, "worker-sessions"],
    queryFn: async () =>
      (
        await readJson<{ data: BoardWorkerSession[] }>(
          await authenticatedFetch("/v1/agent-tasks/worker-sessions"),
        )
      ).data,
    refetchInterval: BOARD_REFRESH_MS,
    retry: false,
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
