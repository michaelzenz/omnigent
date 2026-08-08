import { useState } from "react";
import { ChevronDownIcon, ChevronRightIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { TaskExecutionSummary, TaskWorkerGroup } from "@/lib/agentTasksApi";
import type { AvailableAgent } from "@/hooks/useAvailableAgents";
import { executionSubtitle, WorkStateBadge } from "./TaskCardSessions";
import {
  getFoldedExecutions,
  sortExecutions,
  sortWorkerGroups,
  WORK_ITEM_SCROLL_THRESHOLD,
  WORKER_GROUP_SCROLL_THRESHOLD,
} from "./taskCardUtils";

interface TaskCardWorkProps {
  workers: TaskWorkerGroup[];
  agents: AvailableAgent[];
  selectedExecutionId?: string | null;
  onSelectExecution?: (executionId: string) => void;
}

function workerDisplayName(workerAgentId: string, agents: AvailableAgent[]): string {
  const match = agents.find((agent) => agent.id === workerAgentId);
  return match?.display_name ?? match?.name ?? workerAgentId;
}

function WorkItemRow({
  execution,
  selected,
  onSelect,
}: {
  execution: TaskExecutionSummary;
  selected: boolean;
  onSelect: (executionId: string) => void;
}) {
  const subtitle = executionSubtitle(execution);

  return (
    <li key={execution.id}>
      <button
        type="button"
        onClick={() => onSelect(execution.id)}
        className={cn(
          "flex w-full items-start justify-between gap-2 rounded-md border px-2 py-1 text-left transition-colors",
          selected
            ? "border-ring bg-muted/40 ring-2 ring-ring/40"
            : "border-border/60 bg-muted/20 hover:bg-muted/35",
        )}
        data-testid={`work-item-${execution.id}`}
        aria-pressed={selected}
      >
        <div className="min-w-0">
          <p className="truncate text-sm leading-tight font-medium">
            {execution.event_title ?? "Work item"}
          </p>
          {subtitle ? (
            <p className="mt-px line-clamp-2 text-xs leading-snug text-muted-foreground">
              {subtitle}
            </p>
          ) : null}
        </div>
        <WorkStateBadge status={execution.status} />
      </button>
    </li>
  );
}

function WorkerGroup({
  group,
  agents,
  selectedExecutionId,
  onSelectExecution,
}: {
  group: TaskWorkerGroup;
  agents: AvailableAgent[];
  selectedExecutionId: string | null;
  onSelectExecution: (executionId: string) => void;
}) {
  const executions = sortExecutions(group.executions);
  const [expanded, setExpanded] = useState(false);

  if (executions.length === 0) return null;

  const foldedExecutions = getFoldedExecutions(executions);
  const canToggle = executions.length > 1;
  const visibleExecutions = expanded || !canToggle ? executions : foldedExecutions;
  const hiddenCount = expanded ? 0 : executions.length - visibleExecutions.length;
  const shouldScrollItems = expanded && executions.length > WORK_ITEM_SCROLL_THRESHOLD;
  const workerName = workerDisplayName(group.profile_id, agents);

  return (
    <article
      className="rounded-md border border-border bg-background p-2 shadow-sm"
      data-testid={`worker-group-${group.profile_id}`}
      data-expanded={expanded ? "true" : "false"}
    >
      {canToggle ? (
        <button
          type="button"
          className="mb-1 flex w-full items-center gap-1 text-left"
          onClick={() => setExpanded((open) => !open)}
          aria-expanded={expanded}
          aria-label={expanded ? `Collapse ${workerName} queue` : `Expand ${workerName} queue`}
          data-testid={`worker-group-toggle-${group.profile_id}`}
        >
          {expanded ? (
            <ChevronDownIcon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
          ) : (
            <ChevronRightIcon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
          )}
          <span className="min-w-0 flex-1 truncate text-xs leading-none font-medium text-muted-foreground">
            {workerName}
          </span>
          {hiddenCount > 0 ? (
            <span className="shrink-0 text-[10px] text-muted-foreground">+{hiddenCount}</span>
          ) : null}
        </button>
      ) : (
        <h4 className="mb-1 text-xs leading-none font-medium text-muted-foreground">
          {workerName}
        </h4>
      )}

      {visibleExecutions.length === 0 ? (
        <p className="px-0.5 text-xs text-muted-foreground">No running tasks.</p>
      ) : (
        <ul
          className={cn(
            "space-y-0.5",
            shouldScrollItems && "max-h-40 min-h-0 overflow-y-auto pr-1",
          )}
          data-testid={
            shouldScrollItems
              ? `worker-items-scroll-${group.profile_id}`
              : `worker-items-${group.profile_id}`
          }
        >
          {visibleExecutions.map((execution) => (
            <WorkItemRow
              key={execution.id}
              execution={execution}
              selected={execution.id === selectedExecutionId}
              onSelect={onSelectExecution}
            />
          ))}
        </ul>
      )}
    </article>
  );
}

export function TaskCardWork({
  workers,
  agents,
  selectedExecutionId = null,
  onSelectExecution = () => {},
}: TaskCardWorkProps) {
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
            <WorkerGroup
              key={group.profile_id}
              group={group}
              agents={agents}
              selectedExecutionId={selectedExecutionId}
              onSelectExecution={onSelectExecution}
            />
          ))}
        </div>
      )}
    </section>
  );
}
