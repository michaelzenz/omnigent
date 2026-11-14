import { type ReactNode, useEffect, useState } from "react";
import { ArchiveIcon, CheckIcon, PencilIcon, XIcon } from "lucide-react";
import { MessageResponse } from "@/components/ai-elements/message";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  type AgentTask,
  useArchiveAgentTask,
  useDeleteAgentTaskPermanently,
  useMoveTaskToQueueEnd,
  usePatchAgentTask,
} from "@/hooks/useGlobalHubTasks";
import { relativeTime } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";

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
  task: AgentTask;
  /** The owning manager's name, or ``null`` until the managers load. */
  managerTitle: string | null;
  /** Whether this is the board's last card, which cannot move further down. */
  isLast: boolean;
}

/** One task on the Global Hub board: its header, goal, overview and details. */
export function TaskCard({ task, managerTitle, isLast }: TaskCardProps) {
  const moveToEnd = useMoveTaskToQueueEnd(task.id);
  const resolve = usePatchAgentTask(task.id);
  const resolved = task.state === "agent-resolved";
  const failure = moveToEnd.error ?? resolve.error;

  return (
    <article
      className="flex min-w-0 flex-col rounded-xl border-2 border-border bg-card shadow-sm"
      data-testid={`task-card-${task.id}`}
    >
      <header className="space-y-2 border-b border-border px-4 py-3">
        <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
            <h2 className="min-w-0 text-lg leading-tight font-semibold">{task.title}</h2>
            <Badge
              variant="outline"
              className={cn("shrink-0 border-[1.5px] capitalize", STATE_BADGE_CLASSES[task.state])}
            >
              {resolved ? "resolved" : task.state}
            </Badge>
            {task.manager_id === null && (
              <Badge
                variant="outline"
                title="No manager owns this task"
                className="shrink-0 border-[1.5px] border-[rgba(239,68,68,0.6)] bg-[rgba(239,68,68,0.07)] text-[#b91c1c] dark:bg-[rgba(239,68,68,0.08)] dark:text-[#f87171]"
              >
                Unmanaged
              </Badge>
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
              onClick={() => moveToEnd.mutate()}
              data-testid={`task-card-move-to-end-${task.id}`}
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
                onClick={() => resolve.mutate({ state: "agent-resolved" })}
                data-testid={`task-card-resolve-${task.id}`}
              >
                <CheckIcon className="size-4 text-[#15803d] dark:text-[#4ade80]" aria-hidden />
              </Button>
            )}
            <RemoveTaskButtons task={task} />
          </div>
        </div>
        <EditableGoal task={task} />
        {failure && (
          <p role="alert" className="text-xs text-destructive">
            {failure.message}
          </p>
        )}
      </header>
      <div className="grid min-w-0 gap-4 p-4">
        <section className="min-w-0">
          <h3 className="mb-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
            Overview
          </h3>
          {task.description ? (
            <div className="text-ui break-words">
              <MessageResponse allowGitHubAttachmentImages>{task.description}</MessageResponse>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No overview yet.</p>
          )}
        </section>
        <dl className="grid grid-cols-3 gap-x-4 gap-y-3 rounded-lg border border-border bg-muted/20 p-3">
          <TaskDetail label="Manager">
            {task.manager_id === null ? "None" : (managerTitle ?? "…")}
          </TaskDetail>
          <TaskDetail label="Created">{relativeTime(task.created_at * 1000)}</TaskDetail>
          <TaskDetail label="Priority">P{task.priority}</TaskDetail>
        </dl>
      </div>
    </article>
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

function EditableGoal({ task }: { task: AgentTask }) {
  const patch = usePatchAgentTask(task.id);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(task.goal);
  useEffect(() => {
    if (!editing) setValue(task.goal);
  }, [editing, task.goal]);

  const cancel = () => {
    setValue(task.goal);
    setEditing(false);
  };
  const save = () => {
    const next = value.trim();
    if (!next || next === task.goal) {
      cancel();
      return;
    }
    // A refused goal keeps the editor open with the server's reason.
    patch.mutate({ goal: next }, { onSuccess: () => setEditing(false) });
  };

  if (!editing) {
    return (
      <button
        type="button"
        className="group flex max-w-full items-start gap-1.5 text-left text-sm"
        onClick={() => setEditing(true)}
        data-testid={`task-card-goal-${task.id}`}
      >
        <span className="shrink-0 font-medium">Goal:</span>
        <span className="min-w-0 text-muted-foreground">{task.goal || "Add a goal"}</span>
        <PencilIcon
          className="mt-0.5 size-3.5 shrink-0 opacity-0 group-hover:opacity-100"
          aria-hidden
        />
      </button>
    );
  }

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-1.5">
        <span className="text-sm font-medium">Goal:</span>
        <Input
          autoFocus
          value={value}
          className="h-8 min-w-0 flex-1"
          onChange={(event) => setValue(event.target.value)}
          onBlur={save}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              cancel();
            } else if (event.key === "Enter") {
              event.preventDefault();
              save();
            }
          }}
          data-testid={`task-card-goal-input-${task.id}`}
        />
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label="Save goal"
          disabled={patch.isPending}
          // Keep the input focused so its blur does not save twice.
          onMouseDown={(event) => event.preventDefault()}
          onClick={save}
        >
          <CheckIcon aria-hidden />
        </Button>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label="Cancel goal edit"
          onMouseDown={(event) => event.preventDefault()}
          onClick={cancel}
        >
          <XIcon aria-hidden />
        </Button>
      </div>
      {patch.error && (
        <p role="alert" className="text-xs text-destructive">
          {patch.error.message}
        </p>
      )}
    </div>
  );
}

function RemoveTaskButtons({ task }: { task: AgentTask }) {
  const archive = useArchiveAgentTask(task.id);
  const remove = useDeleteAgentTaskPermanently(task.id);
  const [confirming, setConfirming] = useState<"archive" | "delete" | null>(null);
  const pending = archive.isPending || remove.isPending;
  const close = () => setConfirming(null);
  const onOpenChange = (open: boolean) => {
    if (!open) close();
  };

  return (
    <>
      <Button
        variant="ghost"
        size="icon-sm"
        disabled={pending}
        aria-label="Archive task"
        title="Archive task"
        onClick={() => setConfirming("archive")}
        data-testid={`task-card-archive-${task.id}`}
      >
        <ArchiveIcon className="size-4" aria-hidden />
      </Button>
      <Button
        variant="ghost"
        size="icon-sm"
        className="hover:bg-destructive/10!"
        disabled={pending}
        aria-label="Delete task permanently"
        title="Delete task permanently"
        onClick={() => setConfirming("delete")}
        data-testid={`task-card-delete-${task.id}`}
      >
        <XIcon className="size-4 text-destructive" aria-hidden />
      </Button>
      <ConfirmDialog
        open={confirming === "archive"}
        onOpenChange={onOpenChange}
        title="Archive task?"
        description="The task leaves the board, but its data is kept."
        confirmLabel="Archive"
        pending={archive.isPending}
        error={archive.error}
        onConfirm={() => archive.mutate(undefined, { onSuccess: close })}
      />
      <ConfirmDialog
        open={confirming === "delete"}
        onOpenChange={onOpenChange}
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

interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  confirmLabel: string;
  destructive?: boolean;
  pending: boolean;
  error: Error | null;
  onConfirm: () => void;
}

function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  destructive = false,
  pending,
  error,
  onConfirm,
}: ConfirmDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className={destructive ? "text-destructive" : undefined}>
            {title}
          </DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error.message}
          </p>
        )}
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Cancel</Button>
          </DialogClose>
          <Button
            variant={destructive ? "destructive" : "outline"}
            loading={pending}
            onClick={onConfirm}
            data-testid="task-confirm-button"
          >
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
