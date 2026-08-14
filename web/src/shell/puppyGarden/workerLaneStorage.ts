import type { TaskItemSummary, TaskWorkerLane, TaskWorkerLaneState } from "@/lib/agentTasksApi";

/** Synthetic lane id for unassigned inbox items. */
export const INBOX_LANE_ID = "__inbox__";

const LAST_WORKER_KEY_PREFIX = "puppy-garden:last-worker:";

export function isInboxLane(workerId: string): boolean {
  return workerId === INBOX_LANE_ID;
}

export function buildInboxLane(inboxItems: TaskItemSummary[]): TaskWorkerLane | null {
  if (inboxItems.length === 0) return null;
  const rows = inboxItems.map((item) => ({
    kind: "item" as const,
    item,
    default_folded: false,
    sort_at: item.updated_at ?? item.created_at,
  }));
  rows.sort((a, b) => b.sort_at - a.sort_at);
  const count = inboxItems.length;
  return {
    worker_id: INBOX_LANE_ID,
    role_key: null,
    agent_profile_id: null,
    kind: "managed",
    session_id: null,
    state: "new",
    situation: count === 1 ? "1 unassigned" : `${count} unassigned`,
    rows,
    executions: [],
  };
}

export function lastExpandedWorkerStorageKey(taskId: string): string {
  return `${LAST_WORKER_KEY_PREFIX}${taskId}`;
}

export function readLastExpandedWorker(taskId: string): string | null {
  if (typeof localStorage === "undefined") return null;
  return localStorage.getItem(lastExpandedWorkerStorageKey(taskId));
}

export function writeLastExpandedWorker(taskId: string, workerId: string): void {
  if (typeof localStorage === "undefined") return;
  localStorage.setItem(lastExpandedWorkerStorageKey(taskId), workerId);
}

export function workerLaneStateLabel(state: TaskWorkerLaneState): string {
  if (state === "active") return "Active";
  if (state === "new") return "New";
  return "Idle";
}

export function workerLaneStateClass(state: TaskWorkerLaneState): string {
  if (state === "active") return "border-emerald-200 bg-emerald-50/80";
  if (state === "new") return "border-amber-200 bg-amber-50/80";
  return "border-border bg-background";
}
