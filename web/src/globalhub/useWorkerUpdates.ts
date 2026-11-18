import { useMemo } from "react";
import {
  type AgentTaskCard,
  type AgentTaskWorker,
  type BoardSession,
  useBoardWorkerSessions,
} from "@/hooks/useGlobalHubTasks";
import { useOptionalLoadedConversations } from "@/hooks/useSidebarData";
import {
  isConversationUnseen,
  isExplicitlyUnread,
  isReadStateHydrated,
  type ReadStateSeed,
  useMarkConversationSeen,
  useSeedReadState,
  useUnseenTick,
} from "@/hooks/useUnseenConversations";
import { useGlobalHubChat } from "./GlobalHubChat";

/** A worker, or a session that a worker wraps, with the session's live and read state. */
interface WorkerSessionState {
  session_id: string;
  session: BoardSession;
}

function readStateSeeds(entries: readonly WorkerSessionState[]): ReadStateSeed[] {
  return entries.map(({ session_id: id, session }) => ({
    id,
    viewer_last_seen: session.viewer_last_seen,
    viewer_unread: session.viewer_unread,
    updated_at: session.updated_at,
  }));
}

/**
 * Whether the session has a finished turn that the user has not seen. The open
 * session has none unless the user marked it unread, as on the sidebar's row.
 */
function hasUnseenTurn(
  { session_id: id, session }: WorkerSessionState,
  openSessionId: string | undefined,
): boolean {
  return (
    isConversationUnseen(id, session.updated_at, session.status) &&
    (id !== openSessionId || isExplicitlyUnread(id))
  );
}

/**
 * Keeps the board's worker sessions in the read state behind the sidebar's
 * "new messages" dots. A session outside the sidebar's loaded rows gets its read
 * state from the board. The session that the chat panel shows counts as seen,
 * as the session's own page does.
 */
export function useBoardReadState(cards: AgentTaskCard[] | undefined): void {
  const workers = useMemo(() => cards?.flatMap((card) => card.workers), [cards]);
  const seeds = useMemo(() => workers && readStateSeeds(workers), [workers]);
  useSeedReadState(seeds);
  const sessionId = useGlobalHubChat().target?.sessionId;
  const workerSessions = useBoardWorkerSessions().data;
  const sidebarRows = useOptionalLoadedConversations()?.pages.flatMap((page) => page.data);
  // Whichever poll sees the session's newest change first marks it seen.
  const changes = [
    ...[...(workers ?? []), ...(workerSessions ?? [])]
      .filter((entry) => entry.session_id === sessionId)
      .map((entry) => entry.session.updated_at),
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
    workers.filter((worker) => hasUnseenTurn(worker, openSessionId)).map((worker) => worker.id),
  );
}

/**
 * Whether a worker on any of the user's live tasks has a finished turn that the
 * user has not seen, for the dot on the Global Hub link. A read of the session
 * anywhere clears it. The sessions give read state only after the first seed,
 * so that a reload keeps an unread mark that the session list has not sent yet.
 */
export function useHasWorkerUpdates(openSessionId: string | undefined): boolean {
  useUnseenTick();
  const sessions = useBoardWorkerSessions().data;
  const hydrated = isReadStateHydrated();
  const seeds = useMemo(
    () => (hydrated && sessions ? readStateSeeds(sessions) : undefined),
    [hydrated, sessions],
  );
  useSeedReadState(seeds);
  return (sessions ?? []).some((entry) => hasUnseenTurn(entry, openSessionId));
}
