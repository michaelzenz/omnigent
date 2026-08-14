import { Loader2Icon } from "lucide-react";
import { useAgentTaskList } from "@/hooks/useAgentTasks";
import { usePuppyGardenChat } from "./PuppyGardenChatContext";
import { BoardFyiStream } from "./BoardFyiStream";
import { TaskCard } from "./TaskCard";
import { isPuppyGardenFixtureMode } from "./fixtures/puppyGardenFixtureMode";

export function PuppyGardenBoard() {
  const fixtureMode = isPuppyGardenFixtureMode();
  const { dismissToRole } = usePuppyGardenChat();
  const {
    data: pendingTasks,
    isLoading: pendingLoading,
    error: pendingError,
  } = useAgentTaskList("pending");
  const {
    data: activeTasks,
    isLoading: activeLoading,
    error: activeError,
  } = useAgentTaskList("live");

  const isLoading = pendingLoading || activeLoading;
  const error = pendingError ?? activeError;

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

  const hasPending = (pendingTasks?.length ?? 0) > 0;
  const hasActive = (activeTasks?.length ?? 0) > 0;

  return (
    <div
      className="h-full overflow-y-auto p-4"
      onClick={() => dismissToRole()}
      data-testid="puppy-garden-board-scroll"
    >
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-4">
        {fixtureMode ? (
          <div
            className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-950"
            data-testid="puppy-garden-fixture-banner"
          >
            Fixture mode — dummy board data. Remove <code className="text-xs">?fixture=1</code> from
            the URL to load live tasks.
          </div>
        ) : null}
        <BoardFyiStream />
        {hasPending ? (
          <section className="space-y-3" data-testid="board-pending-tasks">
            <h2 className="text-sm font-semibold text-amber-900">Pending packages</h2>
            {pendingTasks?.map((task) => (
              <TaskCard
                key={task.id}
                taskId={task.id}
                title={task.title}
                description={task.description}
                state={task.state}
                managerRoleKey={task.manager_role_key}
              />
            ))}
          </section>
        ) : null}
        {hasActive ? (
          <section className="space-y-3" data-testid="board-active-tasks">
            <h2 className="text-sm font-semibold text-emerald-800">Tasks</h2>
            {activeTasks?.map((task) => (
              <TaskCard
                key={task.id}
                taskId={task.id}
                title={task.title}
                description={task.description}
                state={task.state}
                managerRoleKey={task.manager_role_key}
              />
            ))}
          </section>
        ) : !hasPending ? (
          <p className="text-sm text-muted-foreground">No tasks yet.</p>
        ) : null}
      </div>
    </div>
  );
}
