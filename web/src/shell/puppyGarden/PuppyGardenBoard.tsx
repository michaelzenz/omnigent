import { Loader2Icon } from "lucide-react";
import { useAgentTaskList } from "@/hooks/useAgentTasks";
import { BoardFyiStream } from "./BoardFyiStream";
import { TaskCard } from "./TaskCard";

export function PuppyGardenBoard() {
  const { data: pausedTasks, isLoading: pausedLoading, error: pausedError } =
    useAgentTaskList("paused");
  const { data: activeTasks, isLoading: activeLoading, error: activeError } =
    useAgentTaskList("active");

  const isLoading = pausedLoading || activeLoading;
  const error = pausedError ?? activeError;

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        <Loader2Icon className="mr-2 size-4 animate-spin" aria-hidden />
        Loading tasks…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-sm text-destructive">
        Failed to load tasks.
      </div>
    );
  }

  const hasPaused = (pausedTasks?.length ?? 0) > 0;
  const hasActive = (activeTasks?.length ?? 0) > 0;

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-4">
        <BoardFyiStream />
        {hasPaused ? (
          <section className="space-y-3" data-testid="board-paused-tasks">
            <h2 className="text-sm font-semibold text-foreground">New packages</h2>
            {pausedTasks?.map((task) => (
              <TaskCard
                key={task.id}
                taskId={task.id}
                title={task.title}
                description={task.description}
                state={task.state}
              />
            ))}
          </section>
        ) : null}
        {hasActive ? (
          activeTasks?.map((task) => (
            <TaskCard
              key={task.id}
              taskId={task.id}
              title={task.title}
              description={task.description}
              state={task.state}
            />
          ))
        ) : !hasPaused ? (
          <p className="text-sm text-muted-foreground">No active tasks yet.</p>
        ) : null}
      </div>
    </div>
  );
}
