import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDownIcon, ChevronRightIcon, MessageSquareIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useRoleProfiles } from "@/hooks/useRoleProfiles";
import { useActivateWorkerLane } from "@/hooks/useAgentTasks";
import { WORKER_ROLE_PREFIX, type TaskItemSummary, type TaskWorkerLane } from "@/lib/agentTasksApi";
import { usePuppyGardenChat } from "./PuppyGardenChatContext";
import { TaskCardWorkerRows } from "./TaskCardWorkerRows";
import { WorkerLaneRolePicker } from "./WorkerLaneRolePicker";
import { TASK_CARD_INNER_SCROLL_CLASS } from "./taskCardUtils";
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
}

function laneDisplayName(lane: TaskWorkerLane, roleTitleByKey: Map<string, string>): string {
  if (isInboxLane(lane.worker_id)) return "Inbox";
  if (lane.role_key == null) return lane.agent_profile_id ?? "External worker";
  return roleTitleByKey.get(lane.role_key) ?? lane.role_key;
}

export function TaskCardWorkers({ taskId, inboxItems, workers }: TaskCardWorkersProps) {
  const { openWorker, isWorkerSelected } = usePuppyGardenChat();
  const activateWorkerLane = useActivateWorkerLane(taskId);
  const { data: workerRoles = [] } = useRoleProfiles(WORKER_ROLE_PREFIX);
  const roleTitleByKey = useMemo(
    () => new Map(workerRoles.map((role) => [role.role, role.title ?? role.role])),
    [workerRoles],
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
    if (stored && lanes.some((lane) => lane.worker_id === stored)) {
      setExpandedLaneId(stored);
      return;
    }
    if (inboxItems.length > 0) {
      setExpandedLaneId(INBOX_LANE_ID);
      return;
    }
    const active = workers.find((lane) => lane.state === "active");
    if (active) {
      setExpandedLaneId(active.worker_id);
      return;
    }
    setExpandedLaneId(lanes[0]?.worker_id ?? null);
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
      <section className="flex min-h-0 flex-1 flex-col self-stretch overflow-hidden">
        <div className="shrink-0 border-b border-border px-3 py-2">
          <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Workers
          </h3>
        </div>
        <p className="flex-1 p-3 text-sm text-muted-foreground">No worker lanes yet.</p>
      </section>
    );
  }

  return (
    <section className="flex min-h-0 flex-1 flex-col self-stretch overflow-hidden">
      <div className="shrink-0 border-b border-border px-3 py-2">
        <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Workers
        </h3>
      </div>
      <div
        className={cn("flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto p-2")}
        data-testid="task-card-workers"
      >
        {lanes.map((lane) => {
          const expanded = expandedLaneId === lane.worker_id;
          const name = laneDisplayName(lane, roleTitleByKey);
          const workerSelected = isWorkerSelected(taskId, lane.worker_id);
          // A lane without a session has nothing to chat with yet, so it offers
          // the role choice instead.
          const awaitingRole = lane.session_id == null && lane.kind !== "external";

          return (
            <article
              key={lane.worker_id}
              className={cn(
                "shrink-0 overflow-hidden rounded-md border shadow-sm",
                workerLaneStateClass(lane.state),
                workerSelected && "ring-2 ring-primary ring-offset-1",
              )}
              data-testid={`worker-lane-${lane.worker_id}`}
              data-expanded={expanded ? "true" : "false"}
              data-chat-selected={workerSelected ? "true" : "false"}
            >
              <div className="flex w-full shrink-0 items-center gap-1 px-1 py-1">
                <button
                  type="button"
                  className="flex min-w-0 flex-1 items-center gap-2 px-1 py-0.5 text-left"
                  onClick={() => toggleLane(lane.worker_id)}
                  aria-expanded={expanded}
                  data-testid={`worker-lane-toggle-${lane.worker_id}`}
                >
                  {expanded ? (
                    <ChevronDownIcon className="size-3.5 shrink-0 text-muted-foreground" />
                  ) : (
                    <ChevronRightIcon className="size-3.5 shrink-0 text-muted-foreground" />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span
                        className="truncate text-sm font-semibold"
                        data-testid={`worker-lane-name-${lane.worker_id}`}
                      >
                        {name}
                      </span>
                      <Badge variant="outline" className="shrink-0 text-[10px]">
                        {workerLaneStateLabel(lane.state)}
                      </Badge>
                    </div>
                    <p className="truncate text-xs text-muted-foreground">{lane.situation}</p>
                  </div>
                </button>
                {isInboxLane(lane.worker_id) ? null : lane.kind === "external" &&
                  lane.session_id == null ? (
                  <span className="shrink-0 px-2 text-[10px] text-muted-foreground">
                    External — no chat yet
                  </span>
                ) : awaitingRole ? (
                  <div
                    className="flex shrink-0 items-center gap-2"
                    onClick={(event) => event.stopPropagation()}
                  >
                    <WorkerLaneRolePicker
                      taskId={taskId}
                      workerId={lane.worker_id}
                      roleKey={lane.role_key}
                    />
                    <Button
                      type="button"
                      size="sm"
                      className="h-7 shrink-0"
                      disabled={
                        !lane.role_key?.trim() ||
                        (activateWorkerLane.isPending &&
                          activateWorkerLane.variables === lane.worker_id)
                      }
                      data-testid={`worker-lane-activate-${lane.worker_id}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        activateWorkerLane.mutate(lane.worker_id);
                      }}
                    >
                      Activate
                    </Button>
                  </div>
                ) : (
                  <Button
                    type="button"
                    variant={workerSelected ? "default" : "outline"}
                    size="sm"
                    className={cn(
                      "h-7 shrink-0 gap-1 px-2 text-xs",
                      !workerSelected &&
                        "border-primary/30 bg-primary/5 text-primary hover:bg-primary/10",
                    )}
                    aria-label={`Open ${name} chat`}
                    aria-pressed={workerSelected}
                    data-testid={`worker-lane-chat-${lane.worker_id}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      openWorker(taskId, lane.worker_id, lane.session_id, name);
                    }}
                  >
                    <MessageSquareIcon className="size-3.5" />
                    Chat
                  </Button>
                )}
              </div>

              {expanded ? (
                <div
                  className={cn(
                    "border-t border-border/60 px-2 pb-2 pt-1",
                    TASK_CARD_INNER_SCROLL_CLASS,
                  )}
                  data-testid={`worker-lane-rows-scroll-${lane.worker_id}`}
                >
                  <TaskCardWorkerRows
                    taskId={taskId}
                    rows={lane.rows}
                    workerLanes={workers}
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
