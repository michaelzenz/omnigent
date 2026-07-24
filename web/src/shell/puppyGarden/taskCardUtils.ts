import type { DispatchPayload, TaskExecutionSummary, TaskWorkerGroup } from "@/lib/agentTasksApi";

export type WorkStateLabel = "To Run" | "Running" | "Done";

/** Work section scrolls once a task has more than this many worker groups. */
export const WORKER_GROUP_SCROLL_THRESHOLD = 2;

/** Each worker's task-item list scrolls after this many items. */
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
