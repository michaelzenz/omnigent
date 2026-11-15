import { type RefObject, useCallback, useState } from "react";
import { InfiniteScrollSentinel } from "@/components/InfiniteScrollSentinel";
import { Spinner } from "@/components/ui/spinner";
import {
  BOARD_MAX_CARDS,
  BOARD_WINDOW,
  useAgentManagers,
  useGlobalHubBoard,
} from "@/hooks/useGlobalHubTasks";
import { TaskCard } from "./TaskCard";
import { UnmanagedTasksLine } from "./UnmanagedTasksLine";

/**
 * The user's live tasks in board order, one card each. The board reads 20
 * cards, and 20 more each time the end of the list scrolls into view. While
 * some live tasks have no manager, a line above the cards says so.
 *
 * @param scrollRoot - The element the board scrolls in.
 */
export function TaskBoard({ scrollRoot }: { scrollRoot: RefObject<HTMLElement | null> }) {
  const [limit, setLimit] = useState(BOARD_WINDOW);
  const board = useGlobalHubBoard(limit);
  const managers = useAgentManagers();
  const readMore = useCallback(
    () => setLimit((current) => Math.min(current + BOARD_WINDOW, BOARD_MAX_CARDS)),
    [],
  );

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
  if (cards.length === 0) {
    return (
      <p className="p-8 text-center text-sm text-muted-foreground" data-testid="globalhub-no-tasks">
        No tasks yet.
      </p>
    );
  }

  const managerTitles = new Map(
    (managers.data ?? []).map((manager) => [manager.id, manager.title]),
  );
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 p-4" data-testid="globalhub-tasks">
      {unmanagedCount > 0 && (
        // A new count mounts a new line, so a sent request no longer holds the button.
        <UnmanagedTasksLine key={unmanagedCount} count={unmanagedCount} />
      )}
      {cards.map((card, index) => (
        <TaskCard
          key={card.id}
          task={card}
          managerTitle={
            card.manager_id === null ? null : (managerTitles.get(card.manager_id) ?? null)
          }
          isLast={!hasMore && index === cards.length - 1}
        />
      ))}
      {limit < BOARD_MAX_CARDS ? (
        <InfiniteScrollSentinel
          hasMore={hasMore}
          isFetching={board.isPlaceholderData}
          fetchMore={readMore}
          scrollRoot={scrollRoot}
          scopeKey="globalhub-board"
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
