import { useEffect, useRef, useState } from "react";
import { Highlight, HighlightedMarkdown } from "./boardSearchHighlight";
import { CheckIcon, Loader2Icon, MessageSquareIcon, PencilIcon, XIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useMoveTaskToQueueEnd, usePatchAgentTask, useTaskDashboard } from "@/hooks/useAgentTasks";
import { relativeTime } from "@/lib/relativeTime";
import type { AgentTaskBoardMatch } from "@/lib/agentTasksApi";
import { cn } from "@/lib/utils";
import { usePuppyGardenChat } from "./PuppyGardenChatContext";
import { TaskCardSidebar } from "./TaskCardAssets";
import { TaskItemsPanel } from "./TaskCardWorkers";
import { TaskActionsMenu } from "./TaskActionsMenu";

// Task-state badge palette ("tinted outline"): hue-matched border + translucent
// fill, darker text in light mode and brighter tinted text in dark mode.
const TASK_STATE_BADGE_CLASSES: Record<string, string> = {
  active:
    "border-[rgba(34,197,94,0.55)] bg-[rgba(34,197,94,0.07)] text-[#15803d] dark:bg-[rgba(34,197,94,0.08)] dark:text-[#4ade80]",
  "agent-resolved":
    "border-[rgba(59,130,246,0.55)] bg-[rgba(59,130,246,0.07)] text-[#1d4ed8] dark:bg-[rgba(59,130,246,0.08)] dark:text-[#60a5fa]",
  idle: "border-[rgba(100,116,139,0.45)] bg-[rgba(100,116,139,0.06)] text-[#64748b] dark:bg-[rgba(148,163,184,0.06)] dark:text-[#94a3b8]",
  archived:
    "border-[rgba(120,113,108,0.45)] bg-[rgba(120,113,108,0.06)] text-[#78716c] dark:bg-[rgba(120,113,108,0.08)] dark:text-[#a8a29e]",
};

interface TaskCardProps {
  taskId: string;
  title: string;
  description: string | null;
  goal?: string;
  createdAt?: number;
  priority?: number;
  state: string;
  /** Durable manager owning this task, when the board list knows it. */
  managerId?: string | null;
  /** Server-side search match for this task while a search is active; the
   * ids drive the amber rings on matched items/assets/workers. */
  searchMatch?: AgentTaskBoardMatch;
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
        <span className="min-w-0 text-muted-foreground">
          {goal ? <Highlight text={goal} /> : "Add a goal"}
        </span>
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
  managerId,
  searchMatch,
  isLast = false,
  onMovedToEnd,
}: TaskCardProps) {
  // Off-screen cards skip the dashboard fetch/poll (the heaviest per-card
  // work: an aggregate query per card, refetched every 10s for every card on
  // the board). The observer preloads slightly ahead of the viewport so a
  // card is ready before it scrolls in.
  const cardRef = useRef<HTMLElement | null>(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    const el = cardRef.current;
    if (!el) return;
    if (typeof IntersectionObserver === "undefined") {
      setInView(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => setInView(entries.some((entry) => entry.isIntersecting)),
      { rootMargin: "400px 0px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const { data: dashboard, isLoading, error } = useTaskDashboard(taskId, { enabled: inView });
  const { target, openManager, isManagerSelected, dismissToRole } = usePuppyGardenChat();
  const moveToEnd = useMoveTaskToQueueEnd(taskId);
  const managerSelected = isManagerSelected(taskId);
  const selectedWorkerId =
    target.kind === "worker" && target.taskId === taskId ? target.workerId : null;
  const task = dashboard?.task;
  const effectiveGoal = task?.goal ?? goal;
  const effectiveDescription = task?.description ?? description;
  const effectiveCreatedAt = task?.created_at ?? createdAt;
  const effectivePriority = task?.priority ?? priority;
  // The dashboard is fresher than the board list; fall back to the prop only
  // before it loads.
  const effectiveManagerId = task ? (task.manager_id ?? null) : (managerId ?? null);
  const unmanaged = !effectiveManagerId;
  const [managerHoldPending, setManagerHoldPending] = useState(false);
  const [managerHoldError, setManagerHoldError] = useState<string | null>(null);

  return (
    <article
      ref={cardRef}
      className={cn(
        "puppy-task-card @container flex min-w-0 flex-col rounded-xl border-2 border-[#888] bg-card shadow-[0_2px_4px_rgba(0,0,0,0.12)]",
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
              <h2 className="min-w-0 text-lg leading-tight font-semibold">
                <Highlight text={title} />
              </h2>
              <Badge
                variant="outline"
                className={cn(
                  "shrink-0 border-[1.5px] capitalize",
                  TASK_STATE_BADGE_CLASSES[state] ?? "",
                )}
              >
                {state === "agent-resolved" ? "resolved" : state}
              </Badge>
              {unmanaged ? (
                <Badge
                  variant="outline"
                  title="No manager owns this task — use the board config to spawn manager(s)"
                  className="shrink-0 border-[1.5px] border-[rgba(239,68,68,0.6)] bg-[rgba(239,68,68,0.07)] text-[#b91c1c] dark:bg-[rgba(239,68,68,0.08)] dark:text-[#f87171]"
                >
                  Unmanaged
                </Badge>
              ) : null}
              {dashboard?.derived.has_running_workers ? (
                <Loader2Icon
                  className="size-4 animate-spin text-muted-foreground"
                  aria-label="Workers running"
                />
              ) : null}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={isLast || moveToEnd.isPending}
              title={
                isLast
                  ? "This task is already last"
                  : state === "agent-resolved"
                    ? "Move task to queue end"
                    : "Move below other open tasks (stays above resolved)"
              }
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
        </div>
        <EditableGoal taskId={taskId} goal={effectiveGoal} />
      </header>

      {isLoading ? (
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
          {/* Overview and task items stack as two full-width rows; the assets/workers
           * rail keeps its right-hand column. */}
          <div className="grid min-w-0 content-start gap-5">
            <section className="min-w-0 space-y-4">
              <div>
                <h3 className="mb-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                  Overview
                </h3>
                {effectiveDescription ? (
                  <div className="prose prose-sm dark:prose-invert max-w-none break-words">
                    <HighlightedMarkdown>{effectiveDescription}</HighlightedMarkdown>
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">No overview yet.</p>
                )}
              </div>
              <dl className="puppy-task-card-meta grid gap-x-4 gap-y-3 rounded-lg border border-border bg-muted/20 p-3">
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
                        } catch (openError) {
                          setManagerHoldError(
                            openError instanceof Error
                              ? openError.message
                              : "Could not pause manager dispatch",
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
              matchedItemIds={searchMatch ? new Set(searchMatch.item_ids) : null}
            />
          </div>
          <div className="puppy-task-card-rail-cell">
            <TaskCardSidebar
              taskId={taskId}
              assets={dashboard.assets ?? []}
              workers={dashboard.workers}
              hostId={dashboard.workers.find((w) => w.host_id)?.host_id ?? null}
              matchedWorkerIds={searchMatch ? new Set(searchMatch.worker_ids) : null}
              matchedAssetIds={searchMatch ? new Set(searchMatch.asset_ids) : null}
            />
          </div>
        </div>
      ) : null}
    </article>
  );
}
