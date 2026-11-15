import { useMemo } from "react";
import type { AgentTaskCard, AgentTaskWorker } from "@/hooks/useGlobalHubTasks";
import { useOptionalLoadedConversations } from "@/hooks/useSidebarData";
import {
  isConversationUnseen,
  isExplicitlyUnread,
  useMarkConversationSeen,
  useSeedReadState,
  useUnseenTick,
} from "@/hooks/useUnseenConversations";
import { useGlobalHubChat } from "./GlobalHubChat";

/**
 * Keeps the board's worker sessions in the read state behind the sidebar's
 * "new messages" dots. A session outside the sidebar's loaded rows gets its read
 * state from the board. The session that the chat panel shows counts as seen,
 * as the session's own page does.
 */
export function useBoardReadState(cards: AgentTaskCard[] | undefined): void {
  const workers = useMemo(() => cards?.flatMap((card) => card.workers), [cards]);
  const seeds = useMemo(
    () =>
      workers?.map(({ session_id: id, session }) => ({
        id,
        viewer_last_seen: session.viewer_last_seen,
        viewer_unread: session.viewer_unread,
        updated_at: session.updated_at,
      })),
    [workers],
  );
  useSeedReadState(seeds);
  const sessionId = useGlobalHubChat().target?.sessionId;
  const sidebarRows = useOptionalLoadedConversations()?.pages.flatMap((page) => page.data);
  // Whichever poll sees the session's newest change first marks it seen.
  const changes = [
    ...(workers ?? [])
      .filter((worker) => worker.session_id === sessionId)
      .map((worker) => worker.session.updated_at),
    ...(sidebarRows ?? []).filter((row) => row.id === sessionId).map((row) => row.updated_at),
  ];
  useMarkConversationSeen(sessionId, changes.length > 0 ? Math.max(...changes) : undefined);
}

/**
 * The ids of the workers whose session finished a turn that the user has not
 * seen. Workers on one session share its read state, so a read of the session
 * anywhere clears all of them. As on the sidebar's open row, the session that
 * the chat panel shows has no dot unless the user marked it unread.
 */
export function useWorkersWithUpdates(workers: AgentTaskWorker[]): ReadonlySet<string> {
  // Re-render as soon as the user reads a session, not at the next board poll.
  useUnseenTick();
  const openSessionId = useGlobalHubChat().target?.sessionId;
  return new Set(
    workers
      .filter(
        ({ session_id: id, session }) =>
          isConversationUnseen(id, session.updated_at, session.status) &&
          (id !== openSessionId || isExplicitlyUnread(id)),
      )
      .map((worker) => worker.id),
  );
}
