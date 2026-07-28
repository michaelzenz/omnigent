import { Loader2Icon } from "lucide-react";
import { useAgentTaskList } from "@/hooks/useAgentTasks";
import { BoardPendingStream } from "./BoardPendingStream";
import { BoardFyiStream } from "./BoardFyiStream";
import { TaskCard } from "./TaskCard";

export function PuppyGardenBoard() {
  const { data: tasks, isLoading, error } = useAgentTaskList("active");

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

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-4">
        <BoardPendingStream />
        <BoardFyiStream />
        {tasks?.length ? (
          tasks.map((task) => (
            <TaskCard
              key={task.id}
              taskId={task.id}
              title={task.title}
              description={task.description}
              state={task.state}
            />
          ))
        ) : (
          <p className="text-sm text-muted-foreground">No active tasks yet.</p>
        )}
      </div>
    </div>
  );
}
