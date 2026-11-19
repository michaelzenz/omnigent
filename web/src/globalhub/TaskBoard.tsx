import { type RefObject, useCallback, useMemo, useRef } from "react";
import { InfiniteScrollSentinel } from "@/components/InfiniteScrollSentinel";
import { Spinner } from "@/components/ui/spinner";
import {
  type AgentTaskBoard,
  type AgentTaskCard,
  boardSearchWords,
  useAgentManagers,
  useGlobalHubBoard,
} from "@/hooks/useGlobalHubTasks";
import { BOARD_COLUMN_CLASS } from "./boardLayout";
import { TaskCard } from "./TaskCard";
import { UnmanagedTasksLine } from "./UnmanagedTasksLine";
import { BOARD_SEARCH_HIGHLIGHT_CSS, useBoardSearchHighlight } from "./useBoardSearchHighlight";
import { useCardAnchor } from "./useCardAnchor";
import { usePageReadAnchor } from "./usePageReadAnchor";
import { useBoardReadState } from "./useWorkerUpdates";

/** The cards of every page in order; a task that moved down between page reads shows once. */
function boardCards(pages: AgentTaskBoard[]): AgentTaskCard[] {
  const seen = new Set<string>();
  const cards: AgentTaskCard[] = [];
  for (const card of pages.flatMap((page) => page.data)) {
    if (seen.has(card.id)) continue;
    seen.add(card.id);
    cards.push(card);
  }
  return cards;
}

/**
 * The user's live tasks in board order, one card each. The board reads a page
 * of 20 cards, and one more page each time the end of the list scrolls into
 * view. It holds at most five pages: a page read at one end drops the page at
 * the other, and the board reads that page again when its end scrolls into
 * view. While some live tasks have no manager, a line above the cards says so.
 * A search shows only the matching cards, best match first, with their matches
 * marked. A priority filter shows only the cards of that priority. A worker
 * whose session finished a turn that the user has not seen has a dot. When the
 * order of the cards changes, the card that the user looks at stays in place.
 *
 * @param scrollRoot - The element the board scrolls in.
 * @param query - The search, or an empty string to show the whole board.
 * @param priority - The one priority to show, from 0 to 3, or ``null`` for all.
 */
export function TaskBoard({
  scrollRoot,
  query = "",
  priority = null,
}: {
  scrollRoot: RefObject<HTMLElement | null>;
  query?: string;
  priority?: number | null;
}) {
  const board = useGlobalHubBoard(query, priority);
  const managers = useAgentManagers();
  const cardsRef = useRef<HTMLDivElement>(null);
  const markAnchor = usePageReadAnchor(
    scrollRoot,
    cardsRef,
    board.isFetchingNextPage || board.isFetchingPreviousPage,
  );
  const { fetchNextPage, fetchPreviousPage } = board;
  const readMore = useCallback(() => {
    markAnchor("last");
    return fetchNextPage({ cancelRefetch: false });
  }, [fetchNextPage, markAnchor]);
  const readEarlier = useCallback(() => {
    markAnchor("first");
    return fetchPreviousPage({ cancelRefetch: false });
  }, [fetchPreviousPage, markAnchor]);
  const pages = board.data?.pages;
  const cards = useMemo(() => (pages ? boardCards(pages) : undefined), [pages]);
  const words = useMemo(() => boardSearchWords(query), [query]);
  const listRef = useRef<HTMLDivElement>(null);
  useBoardSearchHighlight(listRef, words, (cards?.length ?? 0) > 0);
  useBoardReadState(cards);
  const scope = `${query}:${priority ?? "all"}`;
  const order = useMemo(() => (cards ?? []).map((card) => card.id), [cards]);
  const anchorScope = board.isPlaceholderData ? `${scope}:placeholder` : scope;
  const markMoveDown = useCardAnchor(scrollRoot, order, anchorScope);

  if (board.data === undefined || cards === undefined) {
    return board.isError ? (
      <p role="alert" className="p-8 text-center text-sm text-destructive">
        Could not load the tasks: {board.error.message}
      </p>
    ) : (
      <div className="flex justify-center p-8">
        <Spinner className="size-5 text-muted-foreground" aria-label="Loading tasks" />
      </div>
    );
  }
  const { pages: shownPages } = board.data;
  const hasEarlier = Boolean(shownPages[0]?.prev_cursor);
  const hasMore = Boolean(shownPages[shownPages.length - 1]?.next_cursor);
  const unmanagedCount = shownPages[0]?.unmanaged_count ?? 0;
  const searching = words.length > 0;
  if (cards.length === 0) {
    const tasks = priority === null ? "tasks" : `P${priority} tasks`;
    return searching ? (
      <p
        className="p-8 text-center text-sm text-muted-foreground"
        data-testid="globalhub-no-matches"
      >
        No {tasks} match "{query}".
      </p>
    ) : (
      <p className="p-8 text-center text-sm text-muted-foreground" data-testid="globalhub-no-tasks">
        {priority === null ? "No tasks yet." : `No ${tasks}.`}
      </p>
    );
  }

  const managersById = new Map((managers.data ?? []).map((manager) => [manager.id, manager]));
  return (
    <div
      ref={listRef}
      className={`${BOARD_COLUMN_CLASS} flex flex-col gap-4 p-4`}
      data-testid="globalhub-tasks"
    >
      {searching && <style>{BOARD_SEARCH_HIGHLIGHT_CSS}</style>}
      {unmanagedCount > 0 && (
        // A new count mounts a new line, so a sent request no longer holds the button.
        <UnmanagedTasksLine key={unmanagedCount} count={unmanagedCount} />
      )}
      <InfiniteScrollSentinel
        hasMore={hasEarlier}
        isFetching={board.isFetchingPreviousPage || board.isPlaceholderData}
        fetchMore={readEarlier}
        scrollRoot={scrollRoot}
        scopeKey={`globalhub-board-earlier:${scope}`}
        maxAutoLoads={Number.POSITIVE_INFINITY}
      />
      <div ref={cardsRef} className="flex flex-col gap-4">
        {cards.map((card, index) => {
          const manager = card.manager_id === null ? undefined : managersById.get(card.manager_id);
          return (
            <TaskCard
              key={card.id}
              task={card}
              managerTitle={manager?.title ?? null}
              managerSessionId={
                manager === undefined || manager.session_missing ? null : manager.conversation_id
              }
              // A search or a priority filter shows part of the board, so none is known to be last.
              isLast={!searching && priority === null && !hasMore && index === cards.length - 1}
              onMoveDown={markMoveDown}
            />
          );
        })}
      </div>
      <InfiniteScrollSentinel
        hasMore={hasMore}
        isFetching={board.isFetchingNextPage || board.isPlaceholderData}
        fetchMore={readMore}
        scrollRoot={scrollRoot}
        scopeKey={`globalhub-board:${scope}`}
        maxAutoLoads={Number.POSITIVE_INFINITY}
      />
    </div>
  );
}
