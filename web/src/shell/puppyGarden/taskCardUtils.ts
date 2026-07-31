import type { DispatchPayload, TaskExecutionSummary, TaskWorkerGroup } from "@/lib/agentTasksApi";

export type WorkStateLabel = "To Run" | "Running" | "Done";

/** Minimum task card body height in pixels. */
export const TASK_CARD_BODY_MIN_PX = 320;

/** Maximum task card body height in pixels. */
export const TASK_CARD_BODY_MAX_PX = 480;

export const TASK_CARD_BODY_MIN_CLASS = "min-h-[320px]";
export const TASK_CARD_BODY_MAX_CLASS = "max-h-[480px]";
export const TASK_CARD_BODY_CLASS = `${TASK_CARD_BODY_MIN_CLASS} ${TASK_CARD_BODY_MAX_CLASS}`;

/** Outer lists (worker lanes, assets) fill the body column and scroll. */
export const TASK_CARD_SCROLLABLE_LIST_CLASS = "min-h-0 flex-1 overflow-y-auto";

/** Space above expanded lane rows (workers header, list padding, lane header). */
export const TASK_CARD_WORKERS_CHROME = "6.5rem";

/** Reserve space below an expanded lane so the next lane title stays visible. */
export const TASK_CARD_NEXT_LANE_PEEK = "3.5rem";

/** Expanded lane row lists fill the body and scroll when needed. */
export const TASK_CARD_INNER_SCROLL_CLASS =
  "min-h-0 max-h-[calc(var(--task-card-body-max)-var(--task-card-workers-chrome)-var(--task-card-next-lane-peek))] overflow-y-auto";

/** Assets panel width — kept narrow so worker lanes have more horizontal room. */
export const TASK_CARD_ASSETS_WIDTH_CLASS = "w-[220px]";

export function taskCardBodyStyle(): Record<string, string> {
  return {
    "--task-card-body-max": `${TASK_CARD_BODY_MAX_PX}px`,
    "--task-card-workers-chrome": TASK_CARD_WORKERS_CHROME,
    "--task-card-next-lane-peek": TASK_CARD_NEXT_LANE_PEEK,
  };
}

export function isTaskCardSparse(dashboard: {
  inbox_items: unknown[];
  workers: unknown[];
  assets: unknown[];
}): boolean {
  return (
    dashboard.inbox_items.length === 0 &&
    dashboard.workers.length === 0 &&
    dashboard.assets.length === 0
  );
}

/** Work section scrolls once a task has more than this many worker groups. */
export const WORKER_GROUP_SCROLL_THRESHOLD = 2;

/** Each worker's task-item list scrolls after this many items (legacy TaskCardWork). */
export const WORK_ITEM_SCROLL_THRESHOLD = 2;

export interface WorkerOption {
  workerAgentId: string;
  model: string;
}

export function workStateLabel(status: string): WorkStateLabel {
  if (status === "running") return "Running";
  if (status === "queued") return "To Run";
  return "Done";
}

export function isWorkStateActive(label: WorkStateLabel): boolean {
  return label === "Running" || label === "To Run";
}

function executionSortRank(status: string): number {
  if (status === "running") return 0;
  if (status === "queued") return 1;
  return 2;
}

export function sortExecutions(executions: TaskExecutionSummary[]): TaskExecutionSummary[] {
  return [...executions].sort((a, b) => {
    const rank = executionSortRank(a.status) - executionSortRank(b.status);
    if (rank !== 0) return rank;
    // FIFO within each bucket — workers process items in receive order.
    return a.assigned_at - b.assigned_at;
  });
}

function workerGroupRank(group: TaskWorkerGroup): number {
  const statuses = group.executions.map((row) => row.status);
  if (statuses.some((status) => status === "running")) return 0;
  if (statuses.some((status) => status === "queued")) return 1;
  return 2;
}

export function sortWorkerGroups(groups: TaskWorkerGroup[]): TaskWorkerGroup[] {
  return [...groups].sort((a, b) => workerGroupRank(a) - workerGroupRank(b));
}

/** Folded worker cards show only in-flight executions. */
export function getFoldedExecutions(executions: TaskExecutionSummary[]): TaskExecutionSummary[] {
  return sortExecutions(executions).filter((execution) => execution.status === "running");
}

export function isExecutionEditable(status: string): boolean {
  return status === "queued";
}

export function findExecution(
  workers: TaskWorkerGroup[],
  executionId: string | null,
): TaskExecutionSummary | null {
  if (executionId == null) return null;
  for (const group of workers) {
    for (const execution of group.executions) {
      if (execution.id === executionId) return execution;
    }
  }
  return null;
}

export function buildWorkerOptions(
  workerAgentIds: string[],
  proposalPayload: DispatchPayload,
  defaultModel: string,
): WorkerOption[] {
  const byId = new Map<string, WorkerOption>();

  const add = (workerAgentId: string | undefined, model: string) => {
    if (!workerAgentId) return;
    if (byId.has(workerAgentId)) return;
    byId.set(workerAgentId, { workerAgentId, model });
  };

  add(proposalPayload.worker_agent_id, proposalPayload.model ?? defaultModel);
  for (const workerAgentId of workerAgentIds) {
    add(workerAgentId, defaultModel);
  }

  return Array.from(byId.values());
}

export function workerOptionLabel(
  workerAgentId: string,
  model: string,
  agentNameById: Map<string, string>,
): string {
  const name = agentNameById.get(workerAgentId) ?? workerAgentId;
  return `${name} (${model})`;
}

export function proposalHasEdits(
  baseline: DispatchPayload,
  current: {
    workerAgentId: string;
    title: string;
    instructions: string;
    model: string;
  },
): boolean {
  return (
    baseline.worker_agent_id !== current.workerAgentId ||
    (baseline.title ?? "") !== current.title ||
    (baseline.instructions ?? "") !== current.instructions ||
    (baseline.model ?? "") !== current.model
  );
}
