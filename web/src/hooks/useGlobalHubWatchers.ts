import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useStopAndDeleteConversation } from "@/hooks/useConversations";
import type { RoleSchedule, RoleScheduleSettings } from "@/hooks/useGlobalHubRoleProfiles";
import { authenticatedFetch } from "@/lib/identity";
import { apiErrorFromResponse, updateSession } from "@/lib/sessionsApi";

/** One of the user's watchers: a role session that runs on its schedule and reports changes. */
export interface Watcher {
  id: string;
  name: string;
  description: string;
  /** ``null`` while the watcher's session uses the watcher agent's own model. */
  model: string | null;
  /** ``["manual"]`` when the user replaced the manual template. */
  customized: "manual"[];
  /** ``null`` when the watcher has no schedule, so it never runs. */
  schedule: RoleSchedule | null;
  /** The watcher's current session; ``null`` before its first run. */
  session_id: string | null;
  created_at: number;
  updated_at: number;
  /** Whether it is the built-in fallback watcher, which cannot be deleted. */
  builtin: boolean;
  /**
   * The source types the custodian assigned to the watcher; for the fallback
   * watcher, the followed types that no other watcher covers.
   */
  source_types: string[];
}

/** A watcher with the manual sent on every turn, and the template that a reset restores. */
export interface WatcherDetail extends Watcher {
  manual: string;
  default_manual: string;
}

/** What a new watcher starts from. */
export interface WatcherDefaults {
  manual: string;
  /** The schedule rule of a new watcher, e.g. ``"FREQ=MINUTELY;INTERVAL=10"``. */
  rrule: string;
}

/** A watcher to create; a ``null`` manual or model uses the default. */
export interface NewWatcher {
  name: string;
  description: string;
  model: string | null;
  manual: string | null;
  schedule: RoleScheduleSettings;
}

/** Fields to change; ``null`` restores the default manual or model. */
export interface WatcherChanges {
  name?: string;
  description?: string;
  model?: string | null;
  manual?: string | null;
  schedule?: RoleScheduleSettings;
}

/** A saved watcher, and whether its running session took a new model. */
export interface WatcherSaved {
  watcher: WatcherDetail;
  switched: number;
  notSwitched: string[];
}

/** One item that a watcher tracks, with the offsets that it saw last. */
export interface WatchItem {
  id: string;
  source: string;
  source_key: string;
  title: string;
  offsets: Record<string, string>;
  status: "active" | "done";
  created_at: number;
  checked_at: number;
  changed_at: number | null;
  done_at: number | null;
}

/** A page of a watcher's items, in the order that the watcher started to track them. */
export interface WatchItemPage {
  data: WatchItem[];
  first_id: string | null;
  last_id: string | null;
  has_more: boolean;
  total: number;
}

const WATCHERS_QUERY_KEY = ["globalhub-watchers"] as const;
const WATCH_ITEMS_QUERY_KEY = ["globalhub-watch-items"] as const;
const WATCH_ITEMS_PAGE_SIZE = 50;

function watcherPath(id: string): string {
  return `/v1/agent-tasks/watchers/${encodeURIComponent(id)}`;
}

async function readJson<T>(res: Response): Promise<T> {
  if (!res.ok) throw await apiErrorFromResponse(res);
  return (await res.json()) as T;
}

async function sendJson<T>(path: string, method: "POST" | "PATCH", body: unknown): Promise<T> {
  return readJson<T>(
    await authenticatedFetch(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

/** The user's watchers in creation order, without their manuals. */
export function useWatchers() {
  return useQuery({
    queryKey: WATCHERS_QUERY_KEY,
    queryFn: async () =>
      (await readJson<{ data: Watcher[] }>(await authenticatedFetch("/v1/agent-tasks/watchers")))
        .data,
  });
}

/** One watcher with its manual and the manual template. */
export function useWatcher(id: string) {
  return useQuery({
    queryKey: [...WATCHERS_QUERY_KEY, id],
    queryFn: async () => readJson<WatcherDetail>(await authenticatedFetch(watcherPath(id))),
  });
}

/** The manual template and the schedule rule that a new watcher starts from. */
export function useWatcherDefaults() {
  return useQuery({
    queryKey: ["globalhub-watcher-defaults"],
    queryFn: async () =>
      readJson<WatcherDefaults>(await authenticatedFetch("/v1/agent-tasks/watchers/defaults")),
    staleTime: Infinity,
  });
}

/** Create a watcher; its session starts with its first run. */
export function useCreateWatcher() {
  const queryClient = useQueryClient();
  return useMutation<WatcherDetail, Error, NewWatcher>({
    mutationFn: (watcher) => sendJson<WatcherDetail>("/v1/agent-tasks/watchers", "POST", watcher),
    onSuccess: (watcher) => {
      queryClient.setQueryData([...WATCHERS_QUERY_KEY, watcher.id], watcher);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: WATCHERS_QUERY_KEY }),
  });
}

/**
 * Save changes to a watcher. A new model also goes to the watcher's running
 * session through main's session settings, as for the built-in roles.
 */
export function useSaveWatcher(id: string) {
  const queryClient = useQueryClient();
  return useMutation<WatcherSaved, Error, WatcherChanges>({
    mutationFn: async (changes) => {
      const watcher = await sendJson<WatcherDetail>(watcherPath(id), "PATCH", changes);
      if (!("model" in changes) || watcher.session_id === null) {
        return { watcher, switched: 0, notSwitched: [] };
      }
      try {
        await updateSession(watcher.session_id, { modelOverride: watcher.model });
        return { watcher, switched: 1, notSwitched: [] };
      } catch {
        return { watcher, switched: 0, notSwitched: [watcher.name] };
      }
    },
    onSuccess: ({ watcher }) => {
      queryClient.setQueryData([...WATCHERS_QUERY_KEY, id], watcher);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: WATCHERS_QUERY_KEY }),
  });
}

/** Delete a watcher, then its session through main, as the delete of a manager does. */
export function useDeleteWatcher() {
  const queryClient = useQueryClient();
  const deleteSession = useStopAndDeleteConversation();
  return useMutation<void, Error, string>({
    mutationFn: async (watcherId) => {
      const { conversation_id } = await readJson<{ conversation_id: string | null }>(
        await authenticatedFetch(watcherPath(watcherId), { method: "DELETE" }),
      );
      // Remove the card now, so it does not read the deleted watcher again.
      queryClient.setQueryData<Watcher[]>(WATCHERS_QUERY_KEY, (watchers) =>
        watchers?.filter((watcher) => watcher.id !== watcherId),
      );
      queryClient.removeQueries({ queryKey: [...WATCHERS_QUERY_KEY, watcherId] });
      queryClient.removeQueries({ queryKey: [...WATCH_ITEMS_QUERY_KEY, watcherId] });
      if (conversation_id) await deleteSession.mutateAsync({ id: conversation_id });
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: WATCHERS_QUERY_KEY, exact: true }),
  });
}

/** A watcher's items in pages: the active ones, or with `withDone` the done ones too. */
export function useWatchItems(watcherId: string, withDone: boolean) {
  const status = withDone ? "all" : "active";
  return useInfiniteQuery({
    queryKey: [...WATCH_ITEMS_QUERY_KEY, watcherId, status],
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => {
      const params = new URLSearchParams({ status, limit: String(WATCH_ITEMS_PAGE_SIZE) });
      if (pageParam) params.set("after", pageParam);
      return readJson<WatchItemPage>(
        await authenticatedFetch(`${watcherPath(watcherId)}/items?${params}`, { signal }),
      );
    },
    getNextPageParam: (page) => (page.has_more ? page.last_id : undefined),
  });
}
