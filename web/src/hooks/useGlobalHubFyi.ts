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

const FYI_QUERY_KEY = ["globalhub-fyi"] as const;

async function fetchFyiClusters(): Promise<FyiCluster[]> {
  const res = await authenticatedFetch("/v1/agent-tasks/board/pending");
  if (!res.ok) throw await apiErrorFromResponse(res);
  return ((await res.json()) as { fyi: FyiCluster[] }).fyi;
}

/** The user's pending FYI clusters, oldest first. */
export function useFyiClusters() {
  return useQuery({
    queryKey: FYI_QUERY_KEY,
    queryFn: fetchFyiClusters,
    // Managers file events in the background.
    refetchInterval: 10_000,
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
    onSettled: () => queryClient.invalidateQueries({ queryKey: FYI_QUERY_KEY }),
  });
}
