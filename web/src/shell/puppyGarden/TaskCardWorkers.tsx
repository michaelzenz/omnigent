import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDownIcon, ChevronRightIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { AvailableAgent } from "@/hooks/useAvailableAgents";
import type { TaskItemSummary, TaskWorkerLane } from "@/lib/agentTasksApi";
import { TaskCardWorkerRows } from "./TaskCardWorkerRows";
import {
  laneItemsScrollClass,
  LANE_ITEMS_SCROLL_THRESHOLD,
  workerLanesScrollClass,
  WORKER_LANES_SCROLL_THRESHOLD,
} from "./taskCardUtils";
import {
  buildInboxLane,
  INBOX_LANE_ID,
  isInboxLane,
  readLastExpandedWorker,
  workerLaneStateClass,
  workerLaneStateLabel,
  writeLastExpandedWorker,
} from "./workerLaneStorage";

interface TaskCardWorkersProps {
  taskId: string;
  inboxItems: TaskItemSummary[];
  workers: TaskWorkerLane[];
  agents: AvailableAgent[];
  defaultModel: string;
}

function laneDisplayName(lane: TaskWorkerLane, agents: AvailableAgent[]): string {
  if (isInboxLane(lane.worker_agent_id)) return "Inbox";
  const match = agents.find((agent) => agent.id === lane.worker_agent_id);
  return match?.display_name ?? match?.name ?? lane.worker_agent_id;
}

export function TaskCardWorkers({
  taskId,
  inboxItems,
  workers,
  agents,
  defaultModel,
}: TaskCardWorkersProps) {
  const allAgentIds = useMemo(() => agents.map((agent) => agent.id), [agents]);
  const workerAgentIds = useMemo(
    () => workers.map((lane) => lane.worker_agent_id),
    [workers],
  );

  const lanes = useMemo(() => {
    const inboxLane = buildInboxLane(inboxItems);
    return inboxLane ? [inboxLane, ...workers] : workers;
  }, [inboxItems, workers]);

  const [expandedLaneId, setExpandedLaneId] = useState<string | null>(null);
  const initializedRef = useRef(false);

  useEffect(() => {
    if (initializedRef.current) return;
    initializedRef.current = true;
    if (lanes.length === 0) {
      setExpandedLaneId(null);
      return;
    }
    const stored = readLastExpandedWorker(taskId);
    if (stored && lanes.some((lane) => lane.worker_agent_id === stored)) {
      setExpandedLaneId(stored);
      return;
    }
    if (inboxItems.length > 0) {
      setExpandedLaneId(INBOX_LANE_ID);
      return;
    }
    const active = workers.find((lane) => lane.state === "active");
    if (active) {
      setExpandedLaneId(active.worker_agent_id);
      return;
    }
    setExpandedLaneId(lanes[0]?.worker_agent_id ?? null);
  }, [taskId, lanes, inboxItems.length, workers]);

  const toggleLane = (laneId: string) => {
    setExpandedLaneId((current) => {
      const next = current === laneId ? null : laneId;
      if (next) writeLastExpandedWorker(taskId, next);
      return next;
    });
  };

  if (lanes.length === 0) {
    return (
      <section className="flex min-h-0 flex-1 flex-col px-3 py-2">
        <h3 className="shrink-0 text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Workers
        </h3>
        <p className="mt-1 text-sm text-muted-foreground">No worker lanes yet.</p>
      </section>
    );
  }

  return (
    <section className="flex min-h-0 flex-1 flex-col px-3 py-2">
      <h3 className="mb-1 shrink-0 text-xs font-medium tracking-wide text-muted-foreground uppercase">
        Workers
      </h3>
      <div
        className={cn(
          "min-h-0 space-y-1.5 pr-1",
          workerLanesScrollClass(lanes.length),
          lanes.length > WORKER_LANES_SCROLL_THRESHOLD && "overflow-y-auto",
        )}
        data-testid="task-card-workers"
      >
        {lanes.map((lane) => {
          const expanded = expandedLaneId === lane.worker_agent_id;
          const name = laneDisplayName(lane, agents);
          const rowWorkerAgentIds = isInboxLane(lane.worker_agent_id)
            ? allAgentIds
            : workerAgentIds;

          return (
            <article
              key={lane.worker_agent_id}
              className={cn(
                "overflow-hidden rounded-md border shadow-sm",
                workerLaneStateClass(lane.state),
              )}
              data-testid={`worker-lane-${lane.worker_agent_id}`}
              data-expanded={expanded ? "true" : "false"}
            >
              <button
                type="button"
                className="flex w-full items-center gap-2 px-2 py-1.5 text-left"
                onClick={() => toggleLane(lane.worker_agent_id)}
                aria-expanded={expanded}
                data-testid={`worker-lane-toggle-${lane.worker_agent_id}`}
              >
                {expanded ? (
                  <ChevronDownIcon className="size-3.5 shrink-0 text-muted-foreground" />
                ) : (
                  <ChevronRightIcon className="size-3.5 shrink-0 text-muted-foreground" />
                )}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-semibold">{name}</span>
                    <Badge variant="outline" className="shrink-0 text-[10px]">
                      {workerLaneStateLabel(lane.state)}
                    </Badge>
                  </div>
                  <p className="truncate text-xs text-muted-foreground">{lane.situation}</p>
                </div>
              </button>

              {expanded ? (
                <div
                  className={cn(
                    "border-t border-border/60 px-2 pb-2 pt-1",
                    laneItemsScrollClass(lane.rows.length),
                    lane.rows.length > LANE_ITEMS_SCROLL_THRESHOLD && "overflow-y-auto",
                  )}
                  data-testid={`worker-lane-rows-scroll-${lane.worker_agent_id}`}
                >
                  <TaskCardWorkerRows
                    taskId={taskId}
                    rows={lane.rows}
                    workerAgentIds={rowWorkerAgentIds}
                    agents={agents}
                    defaultModel={defaultModel}
                  />
                </div>
              ) : null}
            </article>
          );
        })}
      </div>
    </section>
  );
}
