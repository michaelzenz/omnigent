import type {
  DispatchPayload,
  TaskExecutionSummary,
  TaskItemSummary,
  TaskWorkerLane,
} from "@/lib/agentTasksApi";

export type WorkStateLabel = "To Run" | "Running" | "Done";

export function isDoneTaskItem(state: string): boolean {
  return state === "done" || state === "cancelled";
}

export function isEditableItemState(state: string): boolean {
  return (
    state === "awaiting_user_ack" ||
    state === "queued" ||
    state === "interrupted" ||
    state === "dispatch_failed"
  );
}

export function isParkedItemState(state: string): boolean {
  return state === "interrupted" || state === "dispatch_failed";
}

export function itemStateLabel(state: string): string {
  switch (state) {
    case "awaiting_user_ack":
      return "Needs ack";
    case "queued":
      return "Queued";
    case "running":
      return "Running";
    case "interrupted":
      return "Interrupted";
    case "dispatch_failed":
      return "Dispatch failed";
    case "done":
      return "Done";
    case "cancelled":
      return "Cancelled";
    default:
      return state.replaceAll("_", " ");
  }
}

export function visibleWorkerRows(rows: import("@/lib/agentTasksApi").TaskWorkerRow[]) {
  return rows.filter((row) => row.kind !== "item" || !isDoneTaskItem(row.item.state));
}

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

export interface WorkerOption {
  workerRoleKey: string;
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

/** Folded worker cards show only in-flight executions. */
export function getFoldedExecutions(executions: TaskExecutionSummary[]): TaskExecutionSummary[] {
  return sortExecutions(executions).filter((execution) => execution.status === "running");
}

export function isExecutionEditable(status: string): boolean {
  return status === "queued";
}

export function findExecution(
  workers: TaskWorkerLane[],
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

export function roleKeyForItem(
  item: TaskItemSummary,
  workers: TaskWorkerLane[],
): string | undefined {
  if (item.worker_id == null) return undefined;
  return workers.find((lane) => lane.worker_id === item.worker_id)?.role_key ?? undefined;
}

export function buildWorkerOptions(
  workerRoleKeys: string[],
  proposalPayload: DispatchPayload,
): WorkerOption[] {
  const byId = new Map<string, WorkerOption>();

  const add = (workerRoleKey: string | undefined) => {
    if (!workerRoleKey) return;
    if (byId.has(workerRoleKey)) return;
    byId.set(workerRoleKey, { workerRoleKey });
  };

  add(proposalPayload.worker_role_key);
  for (const workerRoleKey of workerRoleKeys) {
    add(workerRoleKey);
  }

  return Array.from(byId.values());
}

export function workerOptionLabel(
  workerRoleKey: string,
  roleTitleByKey: Map<string, string>,
): string {
  return roleTitleByKey.get(workerRoleKey) ?? workerRoleKey;
}

export function proposalHasEdits(
  baseline: DispatchPayload & { description?: string },
  current: {
    workerRoleKey: string;
    title: string;
    description: string;
    instructions: string;
  },
): boolean {
  return (
    baseline.worker_role_key !== current.workerRoleKey ||
    (baseline.title ?? "") !== current.title ||
    (baseline.description ?? "") !== current.description ||
    (baseline.instructions ?? "") !== current.instructions
  );
}
