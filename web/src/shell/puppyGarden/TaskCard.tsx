import { useMemo, useState } from "react";
import { Loader2Icon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { useAvailableAgents } from "@/hooks/useAvailableAgents";
import { useTaskDashboard } from "@/hooks/useAgentTasks";
import { TaskCardInbox } from "./TaskCardInbox";
import { TaskCardSidePanel } from "./TaskCardSidePanel";
import { TaskCardWork } from "./TaskCardWork";
import { findExecution } from "./taskCardUtils";

interface TaskCardProps {
  taskId: string;
  title: string;
  description: string | null;
  state: string;
}

export function TaskCard({ taskId, title, description, state }: TaskCardProps) {
  const { data: dashboard, isLoading, error } = useTaskDashboard(taskId);
  const { data: agents = [] } = useAvailableAgents();
  const defaultModel = "composer-2.5";
  const [selectedExecutionId, setSelectedExecutionId] = useState<string | null>(null);

  const selectedExecution = useMemo(
    () => (dashboard ? findExecution(dashboard.workers, selectedExecutionId) : null),
    [dashboard, selectedExecutionId],
  );

  const isActive = state === "active";

  return (
    <article
      className="flex min-h-[280px] flex-col overflow-hidden rounded-lg border border-border bg-white shadow-sm"
      data-testid={`task-card-${taskId}`}
      data-task-state={state}
    >
      <header className="flex items-start justify-between gap-2 border-b border-border bg-white px-3 py-2">
        <div className="min-w-0">
          <h2 className="truncate text-base leading-tight font-semibold">{title}</h2>
          {description ? (
            <p className="mt-0.5 line-clamp-2 text-sm leading-snug text-muted-foreground">{description}</p>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {dashboard?.derived.has_running_workers ? (
            <Loader2Icon className="size-4 animate-spin text-muted-foreground" aria-label="Workers running" />
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
        <div className="flex flex-1 items-center justify-center p-8 text-sm text-muted-foreground">
          <Loader2Icon className="mr-2 size-4 animate-spin" aria-hidden />
          Loading task…
        </div>
      ) : error ? (
        <div className="flex flex-1 items-center justify-center p-8 text-sm text-destructive">
          Failed to load task dashboard.
        </div>
      ) : dashboard ? (
        <div className="flex min-h-0 flex-1">
          <div className="flex min-w-0 flex-1 flex-col">
            <TaskCardInbox
              taskId={taskId}
              inboxItems={dashboard.inbox_items}
              workerGroups={dashboard.workers}
              agents={agents}
              defaultModel={defaultModel}
            />
            <TaskCardWork
              workers={dashboard.workers}
              agents={agents}
              selectedExecutionId={selectedExecutionId}
              onSelectExecution={setSelectedExecutionId}
            />
          </div>
          <TaskCardSidePanel
            dashboard={dashboard}
            taskId={taskId}
            agents={agents}
            defaultModel={defaultModel}
            selectedExecution={selectedExecution}
            onClearSelection={() => setSelectedExecutionId(null)}
          />
        </div>
      ) : null}
    </article>
  );
}
