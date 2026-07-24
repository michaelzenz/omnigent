import { useMemo } from "react";
import { Link } from "react-router-dom";
import { Loader2Icon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { relativeTime } from "@/lib/relativeTime";
import type { TaskDashboard } from "@/lib/agentTasksApi";
import { sortExecutions, workStateLabel } from "./taskCardUtils";

interface TaskCardSessionsProps {
  dashboard: TaskDashboard;
}

interface SessionRow {
  id: string;
  label: string;
  kind: "manager" | "worker";
}

export function TaskCardSessions({ dashboard }: TaskCardSessionsProps) {
  const sessions = useMemo(() => {
    const rows: SessionRow[] = [];
    const seen = new Set<string>();

    const managerId = dashboard.task.manager_conversation_id;
    if (managerId) {
      seen.add(managerId);
      rows.push({ id: managerId, label: "Manager", kind: "manager" });
    }

    for (const group of dashboard.workers) {
      for (const execution of sortExecutions(group.executions)) {
        if (!execution.conversation_id || seen.has(execution.conversation_id)) continue;
        seen.add(execution.conversation_id);
        rows.push({
          id: execution.conversation_id,
          label: execution.event_title ?? `Worker session`,
          kind: "worker",
        });
      }
    }

    return rows;
  }, [dashboard]);

  return (
    <aside className="flex min-h-0 w-[260px] shrink-0 flex-col border-l border-border bg-muted/20">
      <div className="border-b border-border px-2.5 py-1.5">
        <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Sessions
        </h3>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {sessions.length === 0 ? (
          <p className="px-1 text-xs text-muted-foreground">No sessions yet.</p>
        ) : (
          <ul className="space-y-0.5">
            {sessions.map((session) => (
              <li key={session.id}>
                <Link
                  to={`/c/${session.id}`}
                  className="block rounded-md px-1.5 py-1 text-sm hover:bg-muted"
                >
                  <div className="flex items-center gap-2">
                    <Badge variant="outline" className="shrink-0 text-[10px]">
                      {session.kind}
                    </Badge>
                    <span className="min-w-0 truncate">{session.label}</span>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="border-t border-border px-2.5 py-1.5">
        <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Assets
        </h3>
        <p className="mt-0.5 text-xs text-muted-foreground">PRs and notebooks coming soon.</p>
      </div>
    </aside>
  );
}

interface WorkStateBadgeProps {
  status: string;
}

export function WorkStateBadge({ status }: WorkStateBadgeProps) {
  const label = workStateLabel(status);
  const variant =
    label === "Running"
      ? "default"
      : label === "To Run"
        ? "secondary"
        : status === "failed"
          ? "destructive"
          : "outline";

  return (
    <Badge variant={variant} className="shrink-0 text-[10px]">
      {label === "Running" ? (
        <span className="inline-flex items-center gap-1">
          <Loader2Icon className="size-3 animate-spin" aria-hidden />
          Running
        </span>
      ) : (
        label
      )}
    </Badge>
  );
}

export function executionSubtitle(execution: {
  result_summary: string | null;
  error: string | null;
  finished_at: number | null;
  assigned_at: number;
}): string | null {
  if (execution.error) return execution.error;
  if (execution.result_summary) return execution.result_summary;
  const ts = execution.finished_at ?? execution.assigned_at;
  return relativeTime(ts * 1000);
}
