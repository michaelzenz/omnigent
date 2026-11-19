import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authenticatedFetch } from "@/lib/identity";
import { apiErrorFromResponse } from "@/lib/sessionsApi";

/** A source that a session follows: each event about it goes to the session. */
export interface SessionSubscription {
  id: string;
  session_id: string;
  /** The source type, e.g. ``"github.pull_request"``. */
  source_type: string;
  /** The source, e.g. a pull request URL or a ticket key. */
  source_key: string;
  /** Why the session follows it, and how to read a source that is hard to read. */
  note: string | null;
  /** Where a person opens the source when its key is not a web address. */
  url: string | null;
  created_at: number;
}

const SUBSCRIPTIONS_QUERY_KEY = ["session-subscriptions"] as const;
const REFRESH_INTERVAL_MS = 30_000;

function subscriptionsPath(sessionId: string): string {
  return `/v1/sessions/${encodeURIComponent(sessionId)}/subscriptions`;
}

/** The sources that a session follows, oldest first; empty without a session. */
export function useSessionSubscriptions(sessionId: string | null) {
  return useQuery({
    queryKey: [...SUBSCRIPTIONS_QUERY_KEY, sessionId],
    enabled: sessionId !== null,
    retry: false,
    refetchInterval: REFRESH_INTERVAL_MS,
    queryFn: async ({ signal }) => {
      const res = await authenticatedFetch(subscriptionsPath(sessionId as string), { signal });
      if (!res.ok) throw await apiErrorFromResponse(res);
      return ((await res.json()) as { data: SessionSubscription[] }).data;
    },
  });
}

/** Stop a session from following a source. */
export function useUnsubscribeSession(sessionId: string) {
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: async (sourceKey) => {
      const res = await authenticatedFetch(`${subscriptionsPath(sessionId)}/remove`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_key: sourceKey }),
      });
      if (!res.ok) throw await apiErrorFromResponse(res);
    },
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: [...SUBSCRIPTIONS_QUERY_KEY, sessionId] }),
  });
}
