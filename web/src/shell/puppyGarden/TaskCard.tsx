import { Loader2Icon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { useAvailableAgents } from "@/hooks/useAvailableAgents";
import { useTaskDashboard } from "@/hooks/useAgentTasks";
import { usePuppyGardenChat } from "./PuppyGardenChatContext";
import { TaskCardAssets } from "./TaskCardAssets";
import { TaskCardWorkers } from "./TaskCardWorkers";
import { TASK_CARD_BODY_CLASS, isTaskCardSparse, taskCardBodyStyle } from "./taskCardUtils";
import { cn } from "@/lib/utils";

interface TaskCardProps {
  taskId: string;
  title: string;
  description: string | null;
  state: string;
}

export function TaskCard({ taskId, title, description, state }: TaskCardProps) {
  const { data: dashboard, isLoading, error } = useTaskDashboard(taskId);
  const { data: agents = [] } = useAvailableAgents();
  const { openManager, isManagerSelected } = usePuppyGardenChat();
  const defaultModel = "composer-2.5";

  const isActive = state === "active";
  const managerSelected = isManagerSelected(taskId);

  const handleHeaderClick = () => {
    openManager(taskId, dashboard?.task.manager_conversation_id ?? null, title);
  };

  return (
    <article
      className={cn(
        "flex flex-col overflow-hidden rounded-lg border border-border bg-white shadow-sm",
        managerSelected && "ring-2 ring-primary ring-offset-1",
      )}
      data-testid={`task-card-${taskId}`}
      data-task-state={state}
      data-chat-selected={managerSelected ? "true" : "false"}
      onClick={(event) => event.stopPropagation()}
    >
      <header
        role="button"
        tabIndex={0}
        className="flex cursor-pointer items-start justify-between gap-2 border-b border-border bg-white px-3 py-2 hover:bg-muted/30"
        onClick={handleHeaderClick}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            handleHeaderClick();
          }
        }}
        data-testid={`task-card-header-${taskId}`}
      >
        <div className="min-w-0">
          <h2 className="truncate text-base leading-tight font-semibold">{title}</h2>
          {description ? (
            <p className="mt-0.5 line-clamp-2 text-sm leading-snug text-muted-foreground">
              {description}
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {dashboard?.derived.has_running_workers ? (
            <Loader2Icon
              className="size-4 animate-spin text-muted-foreground"
              aria-label="Workers running"
            />
          ) : null}
          <Badge
            variant={isActive ? "default" : "outline"}
            className={
              isActive
                ? "border-emerald-600 bg-emerald-600 text-white"
                : "border-amber-200 bg-amber-50 text-amber-900"
            }
          >
            {isActive ? "Active" : "New"}
          </Badge>
        </div>
      </header>

      {isLoading ? (
        <div className="flex min-h-[160px] items-center justify-center p-8 text-sm text-muted-foreground">
          <Loader2Icon className="mr-2 size-4 animate-spin" aria-hidden />
          Loading task…
        </div>
      ) : error ? (
        <div className="flex min-h-[160px] items-center justify-center p-8 text-sm text-destructive">
          Failed to load task dashboard.
        </div>
      ) : dashboard ? (
        <div
          className={cn("flex min-h-0 items-stretch overflow-hidden", TASK_CARD_BODY_CLASS)}
          style={taskCardBodyStyle()}
          data-testid="task-card-body"
          data-sparse={isTaskCardSparse(dashboard) ? "true" : "false"}
        >
          <div className="flex min-h-0 min-w-0 flex-1 flex-col self-stretch overflow-hidden">
            <TaskCardWorkers
              taskId={taskId}
              inboxItems={dashboard.inbox_items}
              workers={dashboard.workers}
              agents={agents}
              defaultModel={defaultModel}
            />
          </div>
          <TaskCardAssets assets={dashboard.assets ?? []} />
        </div>
      ) : null}
    </article>
  );
}
