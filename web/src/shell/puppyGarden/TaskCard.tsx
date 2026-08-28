import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CheckIcon, Loader2Icon, MessageSquareIcon, PencilIcon, XIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  useAcceptAgentTaskPackage,
  useMoveTaskToQueueEnd,
  usePatchAgentTask,
  useRejectAgentTaskPackage,
  useTaskDashboard,
} from "@/hooks/useAgentTasks";
import { relativeTime } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";
import { usePuppyGardenChat } from "./PuppyGardenChatContext";
import { TaskCardManagerRolePicker } from "./TaskCardManagerRolePicker";
import { TaskCardSidebar } from "./TaskCardAssets";
import { TaskItemsPanel } from "./TaskCardWorkers";
import { TaskActionsMenu } from "./TaskActionsMenu";

interface TaskCardProps {
  taskId: string;
  title: string;
  description: string | null;
  goal?: string;
  createdAt?: number;
  priority?: number;
  state: string;
  managerRoleKey: string;
  isLast?: boolean;
  onMovedToEnd?: (taskId: string) => () => void;
}

function EditableGoal({ taskId, goal }: { taskId: string; goal: string }) {
  const patchTask = usePatchAgentTask(taskId);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(goal);
  useEffect(() => {
    if (!editing) setValue(goal);
  }, [editing, goal]);

  const cancel = () => {
    setValue(goal);
    setEditing(false);
  };
  const save = async () => {
    const next = value.trim();
    if (!next || next === goal) {
      cancel();
      return;
    }
    try {
      await patchTask.mutateAsync({ goal: next });
      setEditing(false);
    } catch {
      // The mutation rolls the optimistic value back; keep the editor open.
    }
  };

  if (!editing) {
    return (
      <button
        type="button"
        className="group flex max-w-full items-start gap-1.5 text-left text-sm"
        onClick={(event) => {
          event.stopPropagation();
          setEditing(true);
        }}
      >
        <span className="shrink-0 font-medium">Goal:</span>
        <span className="min-w-0 text-muted-foreground">{goal || "Add a goal"}</span>
        <PencilIcon
          className="mt-0.5 size-3.5 shrink-0 opacity-0 group-hover:opacity-100"
          aria-hidden
        />
      </button>
    );
  }

  return (
    <div className="flex items-center gap-1.5" onClick={(event) => event.stopPropagation()}>
      <span className="text-sm font-medium">Goal:</span>
      <Input
        autoFocus
        value={value}
        className="h-8 min-w-0 flex-1"
        onChange={(event) => setValue(event.target.value)}
        onBlur={() => void save()}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            cancel();
          } else if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
            event.preventDefault();
            void save();
          }
        }}
      />
      <Button
        type="button"
        size="icon-sm"
        variant="ghost"
        aria-label="Save goal"
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => void save()}
      >
        <CheckIcon aria-hidden />
      </Button>
      <Button
        type="button"
        size="icon-sm"
        variant="ghost"
        aria-label="Cancel goal edit"
        onMouseDown={(event) => event.preventDefault()}
        onClick={cancel}
      >
        <XIcon aria-hidden />
      </Button>
    </div>
  );
}

export function TaskCard({
  taskId,
  title,
  description,
  goal = "",
  createdAt,
  priority = 2,
  state,
  managerRoleKey,
  isLast = false,
  onMovedToEnd,
}: TaskCardProps) {
  const { data: dashboard, isLoading, error } = useTaskDashboard(taskId);
  const { target, openManager, isManagerSelected, dismissToRole } = usePuppyGardenChat();
  const moveToEnd = useMoveTaskToQueueEnd(taskId);
  const acceptPackage = useAcceptAgentTaskPackage(taskId);
  const rejectPackage = useRejectAgentTaskPackage(taskId);
  const isPending = state === "pending";
  const managerSelected = !isPending && isManagerSelected(taskId);
  const selectedWorkerId =
    target.kind === "worker" && target.taskId === taskId ? target.workerId : null;
  const task = dashboard?.task;
  const effectiveGoal = task?.goal ?? goal;
  const effectiveDescription = task?.description ?? description;
  const effectiveCreatedAt = task?.created_at ?? createdAt;
  const effectivePriority = task?.priority ?? priority;
  const packageActionPending = acceptPackage.isPending || rejectPackage.isPending;
  const [managerHoldPending, setManagerHoldPending] = useState(false);
  const [managerHoldError, setManagerHoldError] = useState<string | null>(null);

  return (
    <article
      className={cn(
        "puppy-task-card @container flex min-w-0 flex-col rounded-xl border border-border bg-card shadow-sm",
        managerSelected && "ring-2 ring-primary ring-offset-1",
      )}
      data-testid={`task-card-${taskId}`}
      data-task-id={taskId}
      tabIndex={-1}
      onClick={() => dismissToRole()}
    >
      <header className="space-y-2 border-b border-border px-4 py-3">
        <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="min-w-0 text-lg leading-tight font-semibold">{title}</h2>
              <Badge variant="outline" className="shrink-0 capitalize">
                {state}
              </Badge>
              {dashboard?.derived.has_running_workers ? (
                <Loader2Icon
                  className="size-4 animate-spin text-muted-foreground"
                  aria-label="Workers running"
                />
              ) : null}
            </div>
          </div>
          {!isPending ? (
            <div className="flex shrink-0 items-center gap-1.5">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={isLast || moveToEnd.isPending}
                title={isLast ? "This task is already last" : "Move task to queue end"}
                onClick={async (event) => {
                  event.stopPropagation();
                  event.currentTarget.blur();
                  const cancelExplicitMove = onMovedToEnd?.(taskId);
                  try {
                    await moveToEnd.mutateAsync();
                  } catch {
                    cancelExplicitMove?.();
                  }
                }}
              >
                {moveToEnd.isPending ? "Moving…" : "Move to queue end"}
              </Button>
              <TaskActionsMenu taskId={taskId} taskState={state} />
            </div>
          ) : (
            <TaskActionsMenu taskId={taskId} taskState={state} />
          )}
        </div>
        {!isPending ? <EditableGoal taskId={taskId} goal={effectiveGoal} /> : null}
      </header>

      {isPending ? (
        <div className="flex flex-wrap items-center gap-2 p-3">
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
            disabled={packageActionPending}
            onClick={(event) => {
              event.stopPropagation();
              rejectPackage.mutate();
            }}
          >
            Dismiss Task
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={!managerRoleKey.trim() || packageActionPending}
            onClick={(event) => {
              event.stopPropagation();
              acceptPackage.mutate();
            }}
          >
            Create Task
          </Button>
        </div>
      ) : isLoading ? (
        <div className="flex min-h-64 items-center justify-center p-8 text-sm text-muted-foreground">
          <Loader2Icon className="mr-2 size-4 animate-spin" />
          Loading task…
        </div>
      ) : error ? (
        <div className="flex min-h-64 items-center justify-center p-8 text-sm text-destructive">
          Failed to load task dashboard.
        </div>
      ) : dashboard ? (
        <div className="puppy-task-card-body grid min-w-0 gap-5 p-4">
          <section className="min-w-0 space-y-4">
            <div>
              <h3 className="mb-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                Overview
              </h3>
              {effectiveDescription ? (
                <div className="prose prose-sm dark:prose-invert max-w-none break-words">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      a: ({ children, ...props }) => (
                        <a {...props} target="_blank" rel="noopener noreferrer">
                          {children}
                        </a>
                      ),
                    }}
                  >
                    {effectiveDescription}
                  </ReactMarkdown>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">No overview yet.</p>
              )}
            </div>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3 rounded-lg border border-border bg-muted/20 p-3">
              <div className="min-w-0">
                <dt className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                  Manager
                </dt>
                <dd className="mt-1">
                  <Button
                    type="button"
                    size="sm"
                    variant={managerSelected ? "default" : "outline"}
                    className="h-auto min-h-9 w-full max-w-full justify-start gap-1.5 whitespace-normal px-2 py-1.5 text-left leading-tight"
                    disabled={managerHoldPending}
                    onClick={async (event) => {
                      event.stopPropagation();
                      setManagerHoldPending(true);
                      setManagerHoldError(null);
                      try {
                        await openManager(taskId, dashboard.task.manager_conversation_id, title);
                      } catch (error) {
                        setManagerHoldError(
                          error instanceof Error ? error.message : "Could not pause manager dispatch",
                        );
                      } finally {
                        setManagerHoldPending(false);
                      }
                    }}
                  >
                    {managerHoldPending ? (
                      <Loader2Icon className="size-4 animate-spin" aria-hidden />
                    ) : (
                      <MessageSquareIcon aria-hidden />
                    )}
                    {managerHoldPending ? "Pausing…" : "Open manager chat"}
                  </Button>
                  {managerHoldError ? (
                    <p className="mt-1 text-xs text-destructive">{managerHoldError}</p>
                  ) : null}
                </dd>
              </div>
              <div>
                <dt className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                  Workers
                </dt>
                <dd className="mt-1 text-sm font-medium">{dashboard.workers.length}</dd>
              </div>
              <div>
                <dt className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                  Created
                </dt>
                <dd className="mt-1 text-sm font-medium">
                  {effectiveCreatedAt ? relativeTime(effectiveCreatedAt * 1000) : "Unknown"}
                </dd>
              </div>
              <div>
                <dt className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                  Priority
                </dt>
                <dd className="mt-1 text-sm font-medium">
                  P{Math.min(3, Math.max(0, effectivePriority))}
                </dd>
              </div>
            </dl>
          </section>
          <TaskItemsPanel
            taskId={taskId}
            dashboard={dashboard}
            selectedWorkerId={selectedWorkerId}
          />
          <TaskCardSidebar
            taskId={taskId}
            assets={dashboard.assets ?? []}
            workers={dashboard.workers}
          />
        </div>
      ) : null}
    </article>
  );
}
