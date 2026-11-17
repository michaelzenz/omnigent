import { type RefObject, useCallback, useMemo, useRef, useState } from "react";
import { InfiniteScrollSentinel } from "@/components/InfiniteScrollSentinel";
import { Spinner } from "@/components/ui/spinner";
import {
  BOARD_MAX_CARDS,
  BOARD_WINDOW,
  boardSearchWords,
  useAgentManagers,
  useGlobalHubBoard,
} from "@/hooks/useGlobalHubTasks";
import { BOARD_COLUMN_CLASS } from "./boardLayout";
import { TaskCard } from "./TaskCard";
import { UnmanagedTasksLine } from "./UnmanagedTasksLine";
import { BOARD_SEARCH_HIGHLIGHT_CSS, useBoardSearchHighlight } from "./useBoardSearchHighlight";
import { useBoardReadState } from "./useWorkerUpdates";

/**
 * The user's live tasks in board order, one card each. The board reads 20
 * cards, and 20 more each time the end of the list scrolls into view. While
 * some live tasks have no manager, a line above the cards says so. A search
 * shows only the matching cards, best match first, with their matches marked.
 * A worker whose session finished a turn that the user has not seen has a dot.
 *
 * @param scrollRoot - The element the board scrolls in.
 * @param query - The search, or an empty string to show the whole board.
 */
export function TaskBoard({
  scrollRoot,
  query = "",
}: {
  scrollRoot: RefObject<HTMLElement | null>;
  query?: string;
}) {
  const [limit, setLimit] = useState(BOARD_WINDOW);
  // A new search starts again from its first cards.
  const [limitQuery, setLimitQuery] = useState(query);
  if (limitQuery !== query) {
    setLimitQuery(query);
    setLimit(BOARD_WINDOW);
  }
  const board = useGlobalHubBoard(limit, query);
  const managers = useAgentManagers();
  const readMore = useCallback(
    () => setLimit((current) => Math.min(current + BOARD_WINDOW, BOARD_MAX_CARDS)),
    [],
  );
  const words = useMemo(() => boardSearchWords(query), [query]);
  const listRef = useRef<HTMLDivElement>(null);
  useBoardSearchHighlight(listRef, words, (board.data?.data.length ?? 0) > 0);
  useBoardReadState(board.data?.data);

  if (board.data === undefined) {
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
  const { data: cards, has_more: hasMore, unmanaged_count: unmanagedCount } = board.data;
  const searching = words.length > 0;
  if (cards.length === 0) {
    return searching ? (
      <p
        className="p-8 text-center text-sm text-muted-foreground"
        data-testid="globalhub-no-matches"
      >
        No tasks match "{query}".
      </p>
    ) : (
      <p className="p-8 text-center text-sm text-muted-foreground" data-testid="globalhub-no-tasks">
        No tasks yet.
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
            // Search results are not in board order, so none is known to be last.
            isLast={!searching && !hasMore && index === cards.length - 1}
          />
        );
      })}
      {limit < BOARD_MAX_CARDS ? (
        <InfiniteScrollSentinel
          hasMore={hasMore}
          isFetching={board.isPlaceholderData}
          fetchMore={readMore}
          scrollRoot={scrollRoot}
          scopeKey={`globalhub-board:${query}`}
          maxAutoLoads={Number.POSITIVE_INFINITY}
        />
      ) : (
        hasMore && (
          <p className="text-center text-sm text-muted-foreground">
            Showing the first {BOARD_MAX_CARDS.toLocaleString()} tasks.
          </p>
        )
      )}
    </div>
  );
}
