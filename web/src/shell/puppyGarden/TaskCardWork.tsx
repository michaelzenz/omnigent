import { Link } from "react-router-dom";
import { cn } from "@/lib/utils";
import type { TaskExecutionSummary, TaskWorkerGroup } from "@/lib/agentTasksApi";
import type { AvailableAgent } from "@/hooks/useAvailableAgents";
import { executionSubtitle, WorkStateBadge } from "./TaskCardSessions";
import {
  sortExecutions,
  sortWorkerGroups,
  WORK_ITEM_SCROLL_THRESHOLD,
  WORKER_GROUP_SCROLL_THRESHOLD,
} from "./taskCardUtils";

interface TaskCardWorkProps {
  workers: TaskWorkerGroup[];
  agents: AvailableAgent[];
}

function workerDisplayName(workerAgentId: string, agents: AvailableAgent[]): string {
  const match = agents.find((agent) => agent.id === workerAgentId);
  return match?.display_name ?? match?.name ?? workerAgentId;
}

function WorkItemRow({ execution }: { execution: TaskExecutionSummary }) {
  const subtitle = executionSubtitle(execution);
  const content = (
    <div className="flex items-start justify-between gap-2 rounded-md border border-border/60 bg-muted/20 px-2 py-1">
      <div className="min-w-0">
        <p className="truncate text-sm leading-tight font-medium">{execution.event_title ?? "Work item"}</p>
        {subtitle ? (
          <p className="mt-px line-clamp-2 text-xs leading-snug text-muted-foreground">{subtitle}</p>
        ) : null}
      </div>
      <WorkStateBadge status={execution.status} />
    </div>
  );

  if (!execution.conversation_id) {
    return <li key={execution.id}>{content}</li>;
  }

  return (
    <li key={execution.id}>
      <Link to={`/c/${execution.conversation_id}`} className="block hover:opacity-90">
        {content}
      </Link>
    </li>
  );
}

function WorkerGroup({
  group,
  agents,
}: {
  group: TaskWorkerGroup;
  agents: AvailableAgent[];
}) {
  const executions = sortExecutions(group.executions);
  if (executions.length === 0) return null;

  const shouldScrollItems = executions.length > WORK_ITEM_SCROLL_THRESHOLD;

  return (
    <article
      className="rounded-md border border-border bg-background p-2 shadow-sm"
      data-testid={`worker-group-${group.worker_agent_id}`}
    >
      <h4 className="mb-1 text-xs leading-none font-medium text-muted-foreground">
        {workerDisplayName(group.worker_agent_id, agents)}
      </h4>
      <ul
        className={cn(
          "space-y-0.5",
          shouldScrollItems && "max-h-40 min-h-0 overflow-y-auto pr-1",
        )}
        data-testid={
          shouldScrollItems
            ? `worker-items-scroll-${group.worker_agent_id}`
            : `worker-items-${group.worker_agent_id}`
        }
      >
        {executions.map((execution) => (
          <WorkItemRow key={execution.id} execution={execution} />
        ))}
      </ul>
    </article>
  );
}

export function TaskCardWork({ workers, agents }: TaskCardWorkProps) {
  const groups = sortWorkerGroups(workers).filter((group) => group.executions.length > 0);
  const shouldScrollGroups = groups.length > WORKER_GROUP_SCROLL_THRESHOLD;

  return (
    <section className="flex min-h-0 flex-1 flex-col px-3 py-2">
      <h3 className="shrink-0 text-xs font-medium tracking-wide text-muted-foreground uppercase">
        Work
      </h3>
      {groups.length === 0 ? (
        <p className="mt-1 text-sm text-muted-foreground">No worker activity yet.</p>
      ) : (
        <div
          className={cn(
            "mt-1 space-y-2",
            shouldScrollGroups && "max-h-56 min-h-0 overflow-y-auto pr-1",
          )}
          data-testid={shouldScrollGroups ? "task-card-work-scroll" : "task-card-work"}
        >
          {groups.map((group) => (
            <WorkerGroup key={group.worker_agent_id} group={group} agents={agents} />
          ))}
        </div>
      )}
    </section>
  );
}
