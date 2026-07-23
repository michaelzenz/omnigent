import { Loader2Icon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { useAvailableAgents } from "@/hooks/useAvailableAgents";
import { useSecretaryProfile, useTaskDashboard } from "@/hooks/useAgentTasks";
import { TaskCardInbox } from "./TaskCardInbox";
import { TaskCardSessions } from "./TaskCardSessions";
import { TaskCardWork } from "./TaskCardWork";

interface TaskCardProps {
  taskId: string;
  title: string;
  description: string | null;
  state: string;
}

export function TaskCard({ taskId, title, description, state }: TaskCardProps) {
  const { data: dashboard, isLoading, error } = useTaskDashboard(taskId);
  const { data: agents = [] } = useAvailableAgents();
  const { data: secretaryProfile } = useSecretaryProfile();
  const defaultModel = secretaryProfile?.model ?? "composer-2.5";

  return (
    <article
      className="flex min-h-[280px] flex-col overflow-hidden rounded-lg border border-border bg-white shadow-sm"
      data-testid={`task-card-${taskId}`}
    >
      <header className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <h2 className="truncate text-base font-semibold">{title}</h2>
          {description ? (
            <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{description}</p>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {dashboard?.derived.has_running_workers ? (
            <Loader2Icon className="size-4 animate-spin text-muted-foreground" aria-label="Workers running" />
          ) : null}
          <Badge variant="outline">{state}</Badge>
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
              proposals={dashboard.pending_proposals}
              workerGroups={dashboard.workers}
              agents={agents}
              defaultModel={defaultModel}
            />
            <TaskCardWork workers={dashboard.workers} agents={agents} />
          </div>
          <TaskCardSessions dashboard={dashboard} />
        </div>
      ) : null}
    </article>
  );
}
