import { Loader2Icon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  useAcceptAgentTaskPackage,
  useRejectAgentTaskPackage,
  useTaskDashboard,
} from "@/hooks/useAgentTasks";
import { usePuppyGardenChat } from "./PuppyGardenChatContext";
import { TaskCardAssets } from "./TaskCardAssets";
import { TaskCardManagerRolePicker } from "./TaskCardManagerRolePicker";
import { TaskCardWorkers } from "./TaskCardWorkers";
import { TASK_CARD_BODY_CLASS, isTaskCardSparse, taskCardBodyStyle } from "./taskCardUtils";
import { cn } from "@/lib/utils";

interface TaskCardProps {
  taskId: string;
  title: string;
  description: string | null;
  state: string;
  managerRoleKey: string;
}

function taskStateBadge(state: string): {
  label: string;
  className: string;
} {
  if (state === "pending") {
    return {
      label: "Pending",
      className: "border-amber-200 bg-amber-50 text-amber-900",
    };
  }
  if (state === "active") {
    return {
      label: "Active",
      className: "border-emerald-600 bg-emerald-600 text-white",
    };
  }
  return {
    label: "Idle",
    className: "border-slate-200 bg-slate-50 text-slate-700",
  };
}

export function TaskCard({ taskId, title, description, state, managerRoleKey }: TaskCardProps) {
  const { data: dashboard, isLoading, error } = useTaskDashboard(taskId);
  const { openManager, isManagerSelected } = usePuppyGardenChat();
  const acceptPackage = useAcceptAgentTaskPackage(taskId);
  const rejectPackage = useRejectAgentTaskPackage(taskId);

  const isPending = state === "pending";
  const isActive = state === "active";
  const managerSelected = !isPending && isManagerSelected(taskId);
  const badge = taskStateBadge(state);
  const canAccept = isPending && managerRoleKey.trim().length > 0;
  const packageActionPending = acceptPackage.isPending || rejectPackage.isPending;

  const handleHeaderClick = () => {
    if (isPending) return;
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
        role={isPending ? undefined : "button"}
        tabIndex={isPending ? undefined : 0}
        className={cn(
          "flex items-start justify-between gap-2 border-b border-border bg-white px-3 py-2",
          isPending ? "cursor-default" : "cursor-pointer hover:bg-muted/30",
        )}
        onClick={isPending ? undefined : handleHeaderClick}
        onKeyDown={
          isPending
            ? undefined
            : (event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  handleHeaderClick();
                }
              }
        }
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
          {!isPending && dashboard?.derived.has_running_workers ? (
            <Loader2Icon
              className="size-4 animate-spin text-muted-foreground"
              aria-label="Workers running"
            />
          ) : null}
          <Badge variant={isActive ? "default" : "outline"} className={badge.className}>
            {badge.label}
          </Badge>
        </div>
      </header>

      {isPending ? (
        <div className="flex items-center gap-2 border-b border-border bg-white px-3 py-2">
          <TaskCardManagerRolePicker
            taskId={taskId}
            managerRoleKey={managerRoleKey}
            editable
            compact
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="shrink-0"
            disabled={packageActionPending}
            onClick={() => rejectPackage.mutate()}
            data-testid={`task-reject-${taskId}`}
          >
            Dismiss Task
          </Button>
          <Button
            type="button"
            size="sm"
            className="shrink-0"
            disabled={!canAccept || packageActionPending}
            onClick={() => acceptPackage.mutate()}
            data-testid={`task-accept-${taskId}`}
          >
            Create Task
          </Button>
        </div>
      ) : null}

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
            />
          </div>
          <TaskCardAssets assets={dashboard.assets ?? []} />
        </div>
      ) : null}
    </article>
  );
}
