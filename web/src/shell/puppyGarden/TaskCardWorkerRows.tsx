import { useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDownIcon, ChevronRightIcon } from "lucide-react";
import type { AvailableAgent } from "@/hooks/useAvailableAgents";
import type { TaskWorkerRow, TaskWorkerLane } from "@/lib/agentTasksApi";
import { isExecutionEditable } from "./taskCardUtils";
import { TaskCardItemEditor } from "./TaskCardItemEditor";
import { WorkStateBadge, executionSubtitle } from "./TaskCardSessions";
import { relativeTime } from "@/lib/relativeTime";

function rowKey(row: TaskWorkerRow): string {
  return row.kind === "item" ? `item:${row.item.id}` : `exec:${row.execution.id}`;
}

function rowTitle(row: TaskWorkerRow): string {
  if (row.kind === "item") return row.item.title;
  return row.execution.event_title ?? row.execution.item?.title ?? "Work item";
}

interface TaskCardWorkerRowsProps {
  taskId: string;
  rows: TaskWorkerRow[];
  workerAgentIds: string[];
  workerLanes: TaskWorkerLane[];
  agents: AvailableAgent[];
  defaultModel: string;
}

export function TaskCardWorkerRows({
  taskId,
  rows,
  workerAgentIds,
  workerLanes,
  agents,
  defaultModel,
}: TaskCardWorkerRowsProps) {
  const [folded, setFolded] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(rows.map((row) => [rowKey(row), row.default_folded])),
  );

  if (rows.length === 0) {
    return <p className="px-1 text-sm text-muted-foreground">No items for this worker yet.</p>;
  }

  return (
    <ul className="space-y-1.5" data-testid="worker-lane-rows">
      {rows.map((row) => {
        const key = rowKey(row);
        const isFolded = folded[key] ?? row.default_folded;
        const title = rowTitle(row);

        return (
          <li
            key={key}
            className="rounded-md border border-border/70 bg-background shadow-sm"
            data-testid={`worker-row-${key}`}
            data-folded={isFolded ? "true" : "false"}
          >
            <button
              type="button"
              className="flex w-full items-center gap-1.5 px-2 py-1.5 text-left"
              onClick={() => setFolded((prev) => ({ ...prev, [key]: !isFolded }))}
              aria-expanded={!isFolded}
            >
              {isFolded ? (
                <ChevronRightIcon className="size-3.5 shrink-0 text-muted-foreground" />
              ) : (
                <ChevronDownIcon className="size-3.5 shrink-0 text-muted-foreground" />
              )}
              <span className="min-w-0 flex-1 truncate text-sm font-medium">{title}</span>
              {row.kind === "execution" ? (
                <WorkStateBadge status={row.execution.status} />
              ) : (
                <span className="shrink-0 text-[10px] text-muted-foreground uppercase">
                  {row.item.state.replaceAll("_", " ")}
                </span>
              )}
            </button>

            {!isFolded ? (
              <div className="border-t border-border/60 px-2 pb-2 pt-1.5">
                {row.kind === "item" ? (
                  row.item.state === "awaiting_user_ack" || row.item.state === "queued" ? (
                    <TaskCardItemEditor
                      taskId={taskId}
                      item={row.item}
                      workerAgentIds={workerAgentIds}
                      workerLanes={workerLanes}
                      agents={agents}
                      defaultModel={defaultModel}
                      mode={row.item.state === "awaiting_user_ack" ? "ack" : "edit"}
                    />
                  ) : (
                    <ReadOnlyItemBody row={row} />
                  )
                ) : (
                  <ReadOnlyExecutionBody row={row} />
                )}
              </div>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function ReadOnlyItemBody({ row }: { row: Extract<TaskWorkerRow, { kind: "item" }> }) {
  const item = row.item;
  return (
    <div className="space-y-1">
      {item.description ? (
        <p className="text-xs leading-snug whitespace-pre-wrap">{item.description}</p>
      ) : null}
      {item.instructions ? (
        <p className="text-xs leading-snug whitespace-pre-wrap text-muted-foreground">
          {item.instructions}
        </p>
      ) : null}
      <p className="text-[10px] text-muted-foreground">
        Updated {relativeTime((item.updated_at ?? item.created_at) * 1000)}
      </p>
    </div>
  );
}

function ReadOnlyExecutionBody({ row }: { row: Extract<TaskWorkerRow, { kind: "execution" }> }) {
  const execution = row.execution;
  const item = execution.item;
  const subtitle = executionSubtitle(execution);
  const editable = isExecutionEditable(execution.status) && item != null;

  return (
    <div className="space-y-1.5">
      {item?.description ? (
        <p className="text-xs leading-snug whitespace-pre-wrap">{item.description}</p>
      ) : null}
      {item?.instructions ? (
        <p className="text-xs leading-snug whitespace-pre-wrap text-muted-foreground">
          {item.instructions}
        </p>
      ) : null}
      {subtitle ? <p className="text-xs text-muted-foreground">{subtitle}</p> : null}
      {execution.conversation_id ? (
        <Link
          to={`/c/${execution.conversation_id}`}
          className="inline-flex text-xs text-primary hover:underline"
        >
          Open worker session
        </Link>
      ) : null}
      {editable && item ? (
        <p className="text-xs text-muted-foreground">Edit from the queued item row when shown.</p>
      ) : null}
    </div>
  );
}
