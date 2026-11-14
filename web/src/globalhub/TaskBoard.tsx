import { Spinner } from "@/components/ui/spinner";
import { useAgentManagers, useLiveAgentTasks } from "@/hooks/useGlobalHubTasks";
import { TaskCard } from "./TaskCard";

/** The user's live tasks in board order, one card each. */
export function TaskBoard() {
  const tasks = useLiveAgentTasks();
  const managers = useAgentManagers();

  if (tasks.isPending) {
    return (
      <div className="flex justify-center p-8">
        <Spinner className="size-5 text-muted-foreground" aria-label="Loading tasks" />
      </div>
    );
  }
  if (tasks.isError) {
    return (
      <p role="alert" className="p-8 text-center text-sm text-destructive">
        Could not load the tasks: {tasks.error.message}
      </p>
    );
  }
  if (tasks.data.length === 0) {
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
      {tasks.data.map((task, index) => (
        <TaskCard
          key={task.id}
          task={task}
          managerTitle={
            task.manager_id === null ? null : (managerTitles.get(task.manager_id) ?? null)
          }
          isLast={index === tasks.data.length - 1}
        />
      ))}
    </div>
  );
}
