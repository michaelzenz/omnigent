import { useCallback, useLayoutEffect, useMemo, useRef } from "react";
import { Loader2Icon } from "lucide-react";
import { useAgentTaskList } from "@/hooks/useAgentTasks";
import type { AgentTaskSummary } from "@/lib/agentTasksApi";
import { usePuppyGardenChat } from "./PuppyGardenChatContext";
import { BoardFyiStream } from "./BoardFyiStream";
import { TaskCard } from "./TaskCard";
import { isPuppyGardenFixtureMode } from "./fixtures/puppyGardenFixtureMode";

function rankTasks(tasks: AgentTaskSummary[]): AgentTaskSummary[] {
  if (!tasks.some((task) => task.queue_rank != null)) return tasks;
  return [...tasks].sort(
    (a, b) =>
      (b.queue_rank ?? Number.MIN_SAFE_INTEGER) - (a.queue_rank ?? Number.MIN_SAFE_INTEGER) ||
      b.id.localeCompare(a.id),
  );
}

export function PuppyGardenBoard() {
  const fixtureMode = isPuppyGardenFixtureMode();
  const { dismissToRole } = usePuppyGardenChat();
  const scrollRef = useRef<HTMLDivElement>(null);
  const anchorRef = useRef<{ id: string; offset: number } | null>(null);
  const explicitMoveRef = useRef<{ movedId: string; successorId: string | null } | null>(null);
  const previousOrderRef = useRef("");
  const {
    data: pendingTasks,
    isLoading: pendingLoading,
    error: pendingError,
  } = useAgentTaskList("pending");
  const {
    data: activeData,
    isLoading: activeLoading,
    error: activeError,
  } = useAgentTaskList("live");
  const allTasks = useMemo(
    () => rankTasks([...(pendingTasks ?? []), ...(activeData ?? [])]),
    [pendingTasks, activeData],
  );
  const orderKey = allTasks.map((task) => task.id).join("|");

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

  const isLoading = pendingLoading || activeLoading;
  const error = pendingError ?? activeError;
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
      onScroll={captureAnchor}
      onClick={() => dismissToRole()}
      data-testid="puppy-garden-board-scroll"
    >
      <div className="mx-auto flex w-full min-w-0 max-w-[100rem] flex-col gap-5">
        {fixtureMode ? (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-950">
            Fixture mode — dummy board data.
          </div>
        ) : null}
        <BoardFyiStream />
        {hasTasks ? (
          <section className="space-y-5" data-testid="board-active-tasks">
            <div>
              <h1 className="text-xl font-semibold">PuppyGarden</h1>
              <p className="text-sm text-muted-foreground">Live board</p>
            </div>
            {allTasks.map((task, index) => (
              <TaskCard
                key={task.id}
                taskId={task.id}
                title={task.title}
                description={task.description}
                goal={task.goal}
                createdAt={task.created_at}
                priority={task.priority}
                state={task.state}
                managerRoleKey={task.manager_role_key}
                isLast={index === allTasks.length - 1}
                onMovedToEnd={markExplicitMove}
              />
            ))}
          </section>
        ) : (
          <p className="text-sm text-muted-foreground">No tasks yet.</p>
        )}
      </div>
    </div>
  );
}
