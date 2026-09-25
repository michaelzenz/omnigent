import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Loader2Icon, SearchIcon, XIcon } from "lucide-react";
import { useAgentTaskList, useAgentTaskBoardSearch } from "@/hooks/useAgentTasks";
import { Input } from "@/components/ui/input";
import { BoardSearchProvider } from "./boardSearchHighlight";
import {
  AGENT_TASK_BOARD_SEARCH_LIMIT,
  type AgentTaskSummary,
  type AgentTaskBoardMatch,
} from "@/lib/agentTasksApi";
import { usePmv2Chat } from "./Pmv2ChatContext";
import { BoardConfigPanel } from "./BoardConfigPanel";
import { BoardFyiStream } from "./BoardFyiStream";
import { TaskCard } from "./TaskCard";
import { isPmv2FixtureMode } from "./fixtures/pmv2FixtureMode";

// Single source of truth for card order: queue_rank from the server (its list
// endpoint orders by queue_rank desc, id desc; new tasks get the highest rank,
// move-to-queue-end parks a live card directly above the resolved block and
// sinks a resolved card to the lowest). No state-based grouping here — idle/
// resolved cards keep their server-assigned position.
// Cards mounted per board page; the sentinel mounts the next page when the
// user scrolls within 800px of the bottom.
const BOARD_PAGE_SIZE = 8;

function rankTasks(tasks: AgentTaskSummary[]): AgentTaskSummary[] {
  if (!tasks.some((task) => task.queue_rank != null)) return tasks;
  return [...tasks].sort(
    (a, b) =>
      (b.queue_rank ?? Number.MIN_SAFE_INTEGER) - (a.queue_rank ?? Number.MIN_SAFE_INTEGER) ||
      b.id.localeCompare(a.id),
  );
}

export function Pmv2Board() {
  const fixtureMode = isPmv2FixtureMode();
  const { dismissToRole } = usePmv2Chat();
  const scrollRef = useRef<HTMLDivElement>(null);
  const anchorRef = useRef<{ id: string; offset: number } | null>(null);
  const explicitMoveRef = useRef<{ movedId: string; successorId: string | null } | null>(null);
  const previousOrderRef = useRef("");
  const {
    data: activeData,
    isLoading: activeLoading,
    error: activeError,
  } = useAgentTaskList("live");
  const allTasks = useMemo(() => rankTasks(activeData ?? []), [activeData]);
  const orderKey = allTasks.map((task) => task.id).join("|");

  // Floating search: the server matches board-visible task text, items,
  // assets, and worker lane text (token-AND, ranked best match first); the
  // board just filters its cards by the returned task ids and rings the
  // matched entities.
  const [searchQuery, setSearchQuery] = useState("");
  const { data: searchResults } = useAgentTaskBoardSearch(searchQuery);
  const searching = searchQuery.trim().length > 0;
  // The server window caps the match list; at the cap, more matches likely
  // exist below — hint at it instead of paginating (pagination comes later).
  const searchTruncated =
    searching && (searchResults?.length ?? 0) >= AGENT_TASK_BOARD_SEARCH_LIMIT;
  const matchesById = useMemo(() => {
    const map = new Map<string, AgentTaskBoardMatch>();
    for (const match of searchResults ?? []) map.set(match.task_id, match);
    return map;
  }, [searchResults]);
  // While the first request for a new query is in flight (no results yet),
  // keep showing everything — same behavior as the command palette — instead
  // of flashing an empty board. Once results arrive, matching cards render
  // in the server's score order (best match first) instead of queue order.
  const filteredTasks = useMemo(() => {
    if (!searching || !searchResults) return allTasks;
    const orderById = new Map<string, number>();
    searchResults.forEach((match, index) => orderById.set(match.task_id, index));
    return allTasks
      .filter((task) => matchesById.has(task.id))
      .sort(
        (a, b) =>
          (orderById.get(a.id) ?? Number.MAX_SAFE_INTEGER) -
          (orderById.get(b.id) ?? Number.MAX_SAFE_INTEGER),
      );
  }, [searching, searchResults, allTasks, matchesById]);
  // New query: back to the first page so results start at the top.
  useEffect(() => {
    setRenderLimit(BOARD_PAGE_SIZE);
  }, [searchQuery]);

  // Rendered in pages: only the first `renderLimit` cards mount. New tasks
  // take the top ranks so freshly-created work is always on the first page;
  // the sentinel mounts the next page as the user scrolls toward it. Keeps
  // the DOM (and each card's dashboard poll) bounded on large boards.
  const [renderLimit, setRenderLimit] = useState(BOARD_PAGE_SIZE);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const visibleTasks = useMemo(
    () => filteredTasks.slice(0, Math.min(renderLimit, filteredTasks.length)),
    [filteredTasks, renderLimit],
  );

  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || renderLimit >= allTasks.length) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setRenderLimit((current) => Math.min(current + BOARD_PAGE_SIZE, allTasks.length));
        }
      },
      { rootMargin: "800px 0px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [renderLimit, allTasks.length]);

  const captureAnchor = useCallback(() => {
    const root = scrollRef.current;
    if (!root) return;
    const rootTop = root.getBoundingClientRect().top;
    const cards = [...root.querySelectorAll<HTMLElement>("[data-task-id]")];
    const card = cards.find((candidate) => candidate.getBoundingClientRect().bottom > rootTop);
    if (card?.dataset.taskId) {
      anchorRef.current = {
        id: card.dataset.taskId,
        offset: card.getBoundingClientRect().top - rootTop,
      };
    }
  }, []);
  // scroll fires far more often than the anchor needs recalculating; run the
  // DOM measurement at most once per frame.
  const anchorRafRef = useRef<number | null>(null);
  const captureAnchorThrottled = useCallback(() => {
    if (anchorRafRef.current != null) return;
    anchorRafRef.current = requestAnimationFrame(() => {
      anchorRafRef.current = null;
      captureAnchor();
    });
  }, [captureAnchor]);
  useEffect(
    () => () => {
      if (anchorRafRef.current != null) cancelAnimationFrame(anchorRafRef.current);
    },
    [],
  );

  useLayoutEffect(() => {
    if (previousOrderRef.current && previousOrderRef.current !== orderKey) {
      const root = scrollRef.current;
      const explicit = explicitMoveRef.current;
      if (root && explicit) {
        if (explicit.successorId) {
          root
            .querySelector<HTMLElement>(`[data-task-id="${CSS.escape(explicit.successorId)}"]`)
            ?.focus({ preventScroll: true });
        }
        explicitMoveRef.current = null;
      } else if (root && anchorRef.current) {
        const anchored = root.querySelector<HTMLElement>(
          `[data-task-id="${CSS.escape(anchorRef.current.id)}"]`,
        );
        if (anchored) {
          const nextOffset =
            anchored.getBoundingClientRect().top - root.getBoundingClientRect().top;
          root.scrollTop += nextOffset - anchorRef.current.offset;
        }
      }
    }
    previousOrderRef.current = orderKey;
    captureAnchor();
  }, [captureAnchor, orderKey]);

  const markExplicitMove = (taskId: string) => {
    const index = allTasks.findIndex((task) => task.id === taskId);
    explicitMoveRef.current = { movedId: taskId, successorId: allTasks[index + 1]?.id ?? null };
    return () => {
      if (explicitMoveRef.current?.movedId === taskId) explicitMoveRef.current = null;
    };
  };

  const isLoading = activeLoading;
  const error = activeError;
  if (isLoading)
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        <Loader2Icon className="mr-2 size-4 animate-spin" />
        Loading tasks…
      </div>
    );
  if (error)
    return (
      <div className="flex h-full items-center justify-center p-6 text-sm text-destructive">
        Failed to load tasks.
      </div>
    );

  const hasTasks = allTasks.length > 0;

  return (
    <div
      ref={scrollRef}
      className="h-full min-w-0 overflow-y-auto p-3 sm:p-4"
      style={{ overflowAnchor: "none" }}
      onScroll={captureAnchorThrottled}
      onClick={() => dismissToRole()}
      data-testid="pmv2-board-scroll"
    >
      <BoardSearchProvider query={searchQuery}>
        <div
          className="sticky top-0 z-20 -mx-3 mb-0 bg-background/95 px-3 py-2 backdrop-blur-sm sm:-mx-4 sm:px-4"
          data-testid="board-search-bar"
        >
          <div className="relative mx-auto w-full max-w-[100rem]">
            <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  event.stopPropagation();
                  setSearchQuery("");
                }
              }}
              onClick={(event) => event.stopPropagation()}
              placeholder="Search tasks — title, goal, items, assets, workers…"
              className="h-8 pl-8 pr-8"
              aria-label="Search tasks"
              data-testid="board-search-input"
            />
            {searching ? (
              <button
                type="button"
                aria-label="Clear search"
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
                onClick={(event) => {
                  event.stopPropagation();
                  setSearchQuery("");
                }}
              >
                <XIcon className="size-3.5" />
              </button>
            ) : null}
          </div>
        </div>
        <div className="mx-auto flex w-full min-w-0 max-w-[100rem] flex-col gap-5">
          {fixtureMode ? (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-950">
              Fixture mode — dummy board data.
            </div>
          ) : null}
          <BoardFyiStream />
          <div className="flex items-start justify-between gap-2">
            <div>
              <h1 className="text-xl font-semibold">GlobalHub</h1>
              <p className="text-sm text-muted-foreground">Live board</p>
            </div>
            <BoardConfigPanel disabled={fixtureMode} />
          </div>
          {hasTasks ? (
            <>
              <section className="space-y-5" data-testid="board-active-tasks">
                {filteredTasks.length === 0 ? (
                  <p
                    className="py-6 text-center text-sm text-muted-foreground"
                    data-testid="board-search-empty"
                  >
                    No tasks match "{searchQuery.trim()}".
                  </p>
                ) : null}
                {visibleTasks.map((task, index) => (
                  <TaskCard
                    key={task.id}
                    taskId={task.id}
                    title={task.title}
                    description={task.description}
                    goal={task.goal}
                    createdAt={task.created_at}
                    priority={task.priority}
                    state={task.state}
                    managerId={task.manager_id}
                    searchMatch={searching ? matchesById.get(task.id) : undefined}
                    isLast={
                      index === visibleTasks.length - 1 && visibleTasks.length === allTasks.length
                    }
                    onMovedToEnd={markExplicitMove}
                  />
                ))}
              </section>
              {visibleTasks.length < filteredTasks.length ? (
                <div
                  ref={sentinelRef}
                  className="flex items-center justify-center py-3 text-xs text-muted-foreground"
                  data-testid="board-pagination-sentinel"
                >
                  Showing {visibleTasks.length} of {filteredTasks.length}
                  {searching ? " matching" : ""} tasks — scroll for more
                </div>
              ) : null}
              {searching ? (
                <p
                  className="py-1 text-center text-xs text-muted-foreground"
                  data-testid="board-search-count"
                >
                  {filteredTasks.length} of {allTasks.length} tasks match.
                </p>
              ) : null}
              {searchTruncated ? (
                <p
                  className="pb-2 text-center text-xs text-amber-600 dark:text-amber-400"
                  data-testid="board-search-truncated"
                >
                  More matches below — showing the first {searchResults?.length} matching tasks.
                  Refine your search to narrow the results.
                </p>
              ) : null}
            </>
          ) : (
            <p className="text-sm text-muted-foreground">No tasks yet.</p>
          )}
        </div>
      </BoardSearchProvider>
    </div>
  );
}
