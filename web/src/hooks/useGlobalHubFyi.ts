import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authenticatedFetch } from "@/lib/identity";
import { apiErrorFromResponse } from "@/lib/sessionsApi";

/** A signal on the user's board, with the fields the FYI stream reads. */
export interface FyiEvent {
  id: string;
  event_type: string;
  /** What happened, e.g. ``"Session turn finished: Fix S3 retries"``. */
  title: string;
  /** The session the event came from, or ``null``. */
  source_session_id: string | null;
  source_key: string | null;
  created_at: number;
}

/** Events that need no task, which a manager filed together for the user to read. */
export interface FyiCluster {
  id: string;
  headline: string;
  /** Why the manager filed the events together. */
  rationale: string | null;
  created_at: number;
  /** The filed events, oldest first. */
  events: FyiEvent[];
}

export interface WatcherProblemSource {
  source: string;
  source_key: string | null;
  reason: string;
}

export interface WatcherProblem {
  watcher_id: string;
  watcher_name: string;
  session_id: string | null;
  sources: WatcherProblemSource[];
  note: string | null;
  run_error: string | null;
  reported_at: number;
}

interface BoardPending {
  fyi: FyiCluster[];
  watcher_problems?: WatcherProblem[];
}

const BOARD_PENDING_QUERY_KEY = ["globalhub-fyi"] as const;

async function fetchBoardPending(): Promise<BoardPending> {
  const res = await authenticatedFetch("/v1/agent-tasks/board/pending");
  if (!res.ok) throw await apiErrorFromResponse(res);
  return (await res.json()) as BoardPending;
}

/** The user's pending FYI clusters, oldest first. */
export function useFyiClusters() {
  return useQuery({
    queryKey: BOARD_PENDING_QUERY_KEY,
    queryFn: fetchBoardPending,
    // Managers file events in the background.
    refetchInterval: 10_000,
    select: (pending) => pending.fyi,
  });
}

export function useWatcherProblems() {
  return useQuery({
    queryKey: BOARD_PENDING_QUERY_KEY,
    queryFn: fetchBoardPending,
    refetchInterval: 10_000,
    select: (pending) => pending.watcher_problems ?? [],
  });
}

/** Dismiss an FYI cluster and its events. */
export function useDismissFyiCluster() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: async (clusterId) => {
      const res = await authenticatedFetch(
        `/v1/fyi-clusters/${encodeURIComponent(clusterId)}/resolve`,
        { method: "POST" },
      );
      if (!res.ok) throw await apiErrorFromResponse(res);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: BOARD_PENDING_QUERY_KEY }),
  });
}

export function useDismissWatcherProblems() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, string[]>({
    mutationFn: async (watcherIds) => {
      const res = await authenticatedFetch("/v1/agent-tasks/board/watcher-problems/dismiss", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ watcher_ids: watcherIds }),
      });
      if (!res.ok) throw await apiErrorFromResponse(res);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: BOARD_PENDING_QUERY_KEY }),
  });
}
