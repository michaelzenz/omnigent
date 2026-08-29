import { useMemo, useState } from "react";
import { SearchIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAgentTaskList, useReassignWorker } from "@/hooks/useAgentTasks";
import type { AgentTaskSummary } from "@/lib/agentTasksApi";
import { cn } from "@/lib/utils";

interface RebindWorkerDialogProps {
  workerId: string;
  workerName: string;
  currentTaskId: string;
  onClose: () => void;
}

export function RebindWorkerDialog({
  workerId,
  workerName,
  currentTaskId,
  onClose,
}: RebindWorkerDialogProps) {
  const { data: tasks = [] } = useAgentTaskList("live");
  const reassign = useReassignWorker();
  const [query, setQuery] = useState("");

  const filtered = useMemo(
    () =>
      tasks.filter(
        (t) =>
          t.id === currentTaskId ||
          t.title.toLowerCase().includes(query.toLowerCase()),
      ),
    [tasks, currentTaskId, query],
  );

  const handleReassign = async (task: AgentTaskSummary) => {
    if (task.id === currentTaskId) return;
    try {
      await reassign.mutateAsync({ workerId, taskId: task.id });
      onClose();
    } catch {
      // error surfaced by mutation state
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/20"
      onClick={onClose}
    >
      <div
        className="w-[440px] max-w-[90vw] rounded-xl border border-border bg-background shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <h3 className="text-sm font-semibold">
            Rebind <span className="font-bold">{workerName}</span>
          </h3>
          <Button variant="ghost" size="icon-sm" onClick={onClose}>
            <XIcon className="size-4" aria-hidden />
          </Button>
        </div>
        <div className="p-5">
          <div className="relative mb-3">
            <SearchIcon
              className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <Input
              className="h-9 pl-9"
              placeholder="Search tasks…"
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <div className="max-h-[280px] space-y-1.5 overflow-y-auto">
            {filtered.map((task) => {
              const isCurrent = task.id === currentTaskId;
              return (
                <button
                  key={task.id}
                  type="button"
                  disabled={isCurrent}
                  onClick={() => void handleReassign(task)}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-lg border border-border px-3 py-2.5 text-left transition-colors",
                    isCurrent
                      ? "cursor-not-allowed border-dashed opacity-50"
                      : "hover:border-primary/50 hover:bg-muted/40",
                  )}
                >
                  <span
                    className={cn(
                      "size-2 shrink-0 rounded-full",
                      task.state === "active" ? "bg-green-500" : "bg-indigo-400",
                    )}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">
                      {task.title}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {task.state}
                    </span>
                  </span>
                  {isCurrent && (
                    <span className="text-xs font-medium text-muted-foreground">
                      current
                    </span>
                  )}
                </button>
              );
            })}
            {filtered.length === 0 && (
              <p className="px-3 py-4 text-center text-sm text-muted-foreground">
                No tasks found.
              </p>
            )}
          </div>
        </div>
        <div className="flex justify-end border-t border-border px-5 py-3">
          <Button variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Button>
        </div>
      </div>
    </div>
  );
}
