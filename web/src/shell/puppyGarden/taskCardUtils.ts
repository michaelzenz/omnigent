import type { DispatchPayload, TaskExecutionSummary, TaskWorkerGroup } from "@/lib/agentTasksApi";

export type WorkStateLabel = "To Run" | "Running" | "Done";

/** Work section scrolls once a task has more than this many worker groups. */
export const WORKER_GROUP_SCROLL_THRESHOLD = 2;

/** Minimum body height for sparse cards (2× loading-state floor). */
export const TASK_CARD_BODY_MIN_CLASS = "min-h-[320px]";

/** Scroll the worker lane list once a task has more than this many lanes. */
export const WORKER_LANES_SCROLL_THRESHOLD = 3;

/** Max height for the scrollable worker lane list. */
export const WORKER_LANES_SCROLL_MAX_CLASS = "max-h-80";

/** Max height for busy task card bodies (many worker lanes). */
export const TASK_CARD_BODY_MAX_CLASS = "max-h-[480px]";

export function taskLaneCount(dashboard: {
  inbox_items: unknown[];
  workers: unknown[];
}): number {
  return dashboard.workers.length + (dashboard.inbox_items.length > 0 ? 1 : 0);
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

export function taskCardBodyClass(
  sparse: boolean,
  laneCount: number,
): string {
  if (sparse) return TASK_CARD_BODY_MIN_CLASS;
  if (laneCount > WORKER_LANES_SCROLL_THRESHOLD) return TASK_CARD_BODY_MAX_CLASS;
  return "";
}

export function workerLanesScrollClass(laneCount: number): string {
  return laneCount > WORKER_LANES_SCROLL_THRESHOLD ? WORKER_LANES_SCROLL_MAX_CLASS : "";
}

/** Apply scroll cap only when a lane has more than this many rows. */
export const LANE_ITEMS_SCROLL_THRESHOLD = 2;

/** Max height for scrollable expanded worker row lists (busy lanes only). */
export const LANE_ITEMS_SCROLL_MAX_CLASS = "max-h-112";

export function laneItemsScrollClass(rowCount: number): string {
  return rowCount > LANE_ITEMS_SCROLL_THRESHOLD ? LANE_ITEMS_SCROLL_MAX_CLASS : "";
}

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
