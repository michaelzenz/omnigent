import { memo, type ReactNode, useState } from "react";
import { CheckIcon, MessageSquareIcon, XIcon } from "lucide-react";
import { MessageResponse } from "@/components/ai-elements/message";
import { RunningDot } from "@/components/RunningDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  type AgentTask,
  type AgentTaskCard,
  useDeleteAgentTaskPermanently,
  useMoveTaskToQueueEnd,
  usePatchAgentTask,
} from "@/hooks/useGlobalHubTasks";
import { getSessionState } from "@/hooks/useSessionState";
import { relativeTime } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";
import { ConfirmDialog } from "./ConfirmDialog";
import { ChatButton } from "./GlobalHubChat";
import { PRIORITY_COLORS } from "./priorityColors";
import { SEARCH_MATCH_CLASSES, TaskCardRail } from "./TaskCardRail";

// A tinted outline per state: a hue-matched border, a light fill and darker text.
const STATE_BADGE_CLASSES: Record<AgentTask["state"], string> = {
  active:
    "border-[rgba(34,197,94,0.55)] bg-[rgba(34,197,94,0.07)] text-[#15803d] dark:bg-[rgba(34,197,94,0.08)] dark:text-[#4ade80]",
  "agent-resolved":
    "border-[rgba(59,130,246,0.55)] bg-[rgba(59,130,246,0.07)] text-[#1d4ed8] dark:bg-[rgba(59,130,246,0.08)] dark:text-[#60a5fa]",
  idle: "border-[rgba(100,116,139,0.45)] bg-[rgba(100,116,139,0.06)] text-[#64748b] dark:bg-[rgba(148,163,184,0.06)] dark:text-[#94a3b8]",
  archived:
    "border-[rgba(120,113,108,0.45)] bg-[rgba(120,113,108,0.06)] text-[#78716c] dark:bg-[rgba(120,113,108,0.08)] dark:text-[#a8a29e]",
};

interface TaskCardProps {
  task: AgentTaskCard;
  /** The owning manager's name, or ``null`` until the managers load. */
  managerTitle: string | null;
  /** The owning manager's session, or ``null`` while it has none. */
  managerSessionId: string | null;
  /** Whether this is the board's last card, which cannot move further down. */
  isLast: boolean;
  /**
   * Called when the user moves the card down the board, before the move; the
   * board calls the returned function if the move fails.
   */
  onMoveDown?: (taskId: string) => () => void;
}

/** One task on the Global Hub board: its header, overview, details, assets and workers. */
export const TaskCard = memo(function TaskCard({
  task,
  managerTitle,
  managerSessionId,
  isLast,
  onMoveDown,
}: TaskCardProps) {
  const moveToEnd = useMoveTaskToQueueEnd(task.id);
  const resolve = usePatchAgentTask(task.id);
  const reprioritize = usePatchAgentTask(task.id);
  const clearNews = usePatchAgentTask(task.id);
  const hasUpdate = task.has_update === true && !clearNews.isPending;
  const resolved = task.state === "agent-resolved";
  const failure = moveToEnd.error ?? resolve.error ?? reprioritize.error ?? clearNews.error;
  const priority = reprioritize.isPending
    ? (reprioritize.variables?.priority ?? task.priority)
    : task.priority;
  const workersRunning = task.workers.some(
    (worker) => getSessionState(worker.session)?.kind === "running",
  );

  return (
    <article
      className="@container flex min-w-0 flex-col rounded-xl border-2 border-border bg-card shadow-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
      data-testid={`task-card-${task.id}`}
      data-task-id={task.id}
      tabIndex={-1}
      onClickCapture={
        hasUpdate
          ? (event) => {
              if (!keepsUpdateFlag(event.target)) clearNews.mutate({ has_update: false });
            }
          : undefined
      }
    >
      <header className="space-y-2 border-b border-border px-4 py-3">
        <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
            <h2
              className={cn(
                "min-w-0 text-lg leading-tight font-semibold",
                task.match?.title_match && cn("rounded-md px-1", SEARCH_MATCH_CLASSES),
              )}
              data-board-search-text
              data-testid={`task-card-title-${task.id}`}
            >
              {task.title}
            </h2>
            <Badge
              variant="outline"
              className={cn("shrink-0 border-[1.5px] capitalize", STATE_BADGE_CLASSES[task.state])}
            >
              {resolved ? "resolved" : task.state}
            </Badge>
            {hasUpdate && (
              <Badge
                variant="outline"
                title="The manager flagged news on this task. Click the card to clear it."
                className="shrink-0 border-[1.5px] border-brand-accent/60 bg-brand-accent/10 text-brand-accent"
              >
                Has update
              </Badge>
            )}
            {task.manager_id === null && (
              <Badge
                variant="outline"
                title="No manager owns this task"
                className="shrink-0 border-[1.5px] border-[rgba(239,68,68,0.6)] bg-[rgba(239,68,68,0.07)] text-[#b91c1c] dark:bg-[rgba(239,68,68,0.08)] dark:text-[#f87171]"
              >
                Unmanaged
              </Badge>
            )}
            {workersRunning && (
              <span
                role="img"
                aria-label="Workers running"
                title="Workers running"
                className="inline-flex"
              >
                <RunningDot className="size-4" />
              </span>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <Button
              variant="outline"
              size="sm"
              disabled={isLast || moveToEnd.isPending}
              title={
                isLast
                  ? "This task is already last"
                  : resolved
                    ? "Move the task to the end of the board"
                    : "Move the task below the other open tasks"
              }
              onClick={() => {
                const cancelMove = onMoveDown?.(task.id);
                moveToEnd.mutate(undefined, { onError: () => cancelMove?.() });
              }}
              data-testid={`task-card-move-to-end-${task.id}`}
              data-keeps-update-flag=""
            >
              {moveToEnd.isPending ? "Moving…" : "Move to queue end"}
            </Button>
            {!resolved && (
              <Button
                variant="ghost"
                size="icon-sm"
                disabled={resolve.isPending}
                aria-label="Mark task resolved"
                title="Mark task resolved"
                onClick={() => {
                  const cancelMove = onMoveDown?.(task.id);
                  resolve.mutate({ state: "agent-resolved" }, { onError: () => cancelMove?.() });
                }}
                data-testid={`task-card-resolve-${task.id}`}
              >
                <CheckIcon className="size-4 text-[#15803d] dark:text-[#4ade80]" aria-hidden />
              </Button>
            )}
            <DeleteTaskButton task={task} />
          </div>
        </div>
        {failure && (
          <p role="alert" className="text-xs text-destructive">
            {failure.message}
          </p>
        )}
      </header>
      <div className="grid min-w-0 gap-4 p-4 @4xl:min-h-64 @4xl:grid-cols-[minmax(0,1fr)_18rem]">
        <div className="grid min-w-0 content-start gap-4">
          <section className="min-w-0">
            <h3 className="mb-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
              Overview
            </h3>
            {task.description ? (
              <div className="text-ui break-words" data-board-search-text>
                <MessageResponse allowGitHubAttachmentImages>{task.description}</MessageResponse>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No overview yet.</p>
            )}
          </section>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3 rounded-lg border border-border bg-muted/20 p-3 @2xl:grid-cols-4">
            <TaskDetail label="Manager">
              <ManagerName task={task} title={managerTitle} sessionId={managerSessionId} />
            </TaskDetail>
            <TaskDetail label="Workers">{task.workers.length}</TaskDetail>
            <TaskDetail label="Created">{relativeTime(task.created_at * 1000)}</TaskDetail>
            <TaskDetail label="Priority">
              <PrioritySelect
                taskId={task.id}
                priority={priority}
                disabled={reprioritize.isPending}
                onChange={(value) => reprioritize.mutate({ priority: value })}
              />
            </TaskDetail>
          </dl>
        </div>
        <div className="relative min-w-0">
          <TaskCardRail task={task} />
        </div>
      </div>
    </article>
  );
});

function keepsUpdateFlag(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest("[data-keeps-update-flag]") !== null;
}

/** The manager's name; while the manager has a session, a button that opens its chat. */
function ManagerName({
  task,
  title,
  sessionId,
}: {
  task: AgentTask;
  title: string | null;
  sessionId: string | null;
}) {
  if (task.manager_id === null) return "None";
  if (title === null) return "…";
  if (sessionId === null) return title;
  return (
    <ChatButton
      target={{ sessionId, title, kind: "Manager", taskId: task.id }}
      label={`Chat with ${title}`}
      variant="outline"
      size="lg"
      className="w-full justify-start font-medium aria-pressed:bg-accent aria-pressed:text-foreground"
      data-keeps-update-flag=""
    >
      <MessageSquareIcon aria-hidden />
      <span className="truncate">{title}</span>
    </ChatButton>
  );
}

/** The task's priority as a colored menu, P0 (red) to P3 (green); a pick saves it at once. */
function PrioritySelect({
  taskId,
  priority,
  disabled,
  onChange,
}: {
  taskId: string;
  priority: number;
  disabled: boolean;
  onChange: (priority: number) => void;
}) {
  return (
    <Select
      value={String(priority)}
      disabled={disabled}
      onValueChange={(value) => onChange(Number(value))}
    >
      <SelectTrigger
        size="sm"
        aria-label="Priority"
        className={cn("border-[1.5px] font-semibold", PRIORITY_COLORS[priority]?.pill)}
        data-testid={`task-card-priority-${taskId}`}
      >
        <SelectValue>
          <PriorityLabel priority={priority} />
        </SelectValue>
      </SelectTrigger>
      <SelectContent>
        {PRIORITY_COLORS.map(({ value }) => (
          <SelectItem
            key={value}
            value={String(value)}
            data-testid={`task-card-priority-option-${value}`}
          >
            <PriorityLabel priority={value} />
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function PriorityLabel({ priority }: { priority: number }) {
  return (
    <span className="flex items-center gap-1.5">
      <span
        aria-hidden
        className={cn("size-2 shrink-0 rounded-full", PRIORITY_COLORS[priority]?.dot)}
      />
      P{priority}
    </span>
  );
}

function TaskDetail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
        {label}
      </dt>
      <dd className="mt-1 truncate text-sm font-medium">{children}</dd>
    </div>
  );
}

function DeleteTaskButton({ task }: { task: AgentTask }) {
  const remove = useDeleteAgentTaskPermanently(task.id);
  const [confirming, setConfirming] = useState(false);
  const close = () => setConfirming(false);

  return (
    <>
      <Button
        variant="ghost"
        size="icon-sm"
        className="hover:bg-destructive/10!"
        disabled={remove.isPending}
        aria-label="Delete task permanently"
        title="Delete task permanently"
        onClick={() => setConfirming(true)}
        data-testid={`task-card-delete-${task.id}`}
      >
        <XIcon className="size-4 text-destructive" aria-hidden />
      </Button>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title="Delete task permanently?"
        description="This cannot be undone. The task is removed with its overview and links. Its sessions stay in your session list."
        confirmLabel="Delete permanently"
        destructive
        pending={remove.isPending}
        error={remove.error}
        onConfirm={() => remove.mutate(undefined, { onSuccess: close })}
      />
    </>
  );
}
