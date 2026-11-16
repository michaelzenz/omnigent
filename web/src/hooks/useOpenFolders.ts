import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { OpenFolder } from "@/lib/events";
import { authenticatedFetch } from "@/lib/identity";
import { onUiOpenFolders, parseOpenFolders } from "@/lib/openFolders";
import { isTempConvId } from "@/lib/tempConversationId";

const NO_FOLDERS: OpenFolder[] = [];

const openFoldersKey = (sessionId: string) => ["open-folders", sessionId] as const;

/** The folders that the session's agent stamped, kept current by the session stream. */
export function useOpenFolders(sessionId: string | null | undefined): OpenFolder[] {
  const client = useQueryClient();
  const enabled = !!sessionId && !isTempConvId(sessionId);
  const { data } = useQuery({
    queryKey: openFoldersKey(sessionId ?? ""),
    enabled,
    staleTime: 60_000,
    // An older server has no folder route; the button then stays hidden.
    retry: false,
    queryFn: async () => {
      const response = await authenticatedFetch(
        `/v1/sessions/${encodeURIComponent(sessionId!)}/ui/open-folders`,
      );
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      const body = (await response.json()) as { folders?: unknown };
      return parseOpenFolders(body.folders) ?? NO_FOLDERS;
    },
  });
  useEffect(() => {
    if (!enabled || !sessionId) return;
    return onUiOpenFolders((event, conversationId) => {
      if (conversationId === sessionId)
        client.setQueryData(openFoldersKey(sessionId), event.folders);
    });
  }, [client, enabled, sessionId]);
  return data ?? NO_FOLDERS;
}
