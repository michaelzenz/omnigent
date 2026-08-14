import type { TaskDashboard, TaskItemSummary } from "@/lib/agentTasksApi";
import { cloneFixtureDashboards } from "./mockTaskDashboard";

type FixtureListener = () => void;

let dashboards = cloneFixtureDashboards();
const listeners = new Set<FixtureListener>();

function notify() {
  for (const listener of listeners) {
    listener();
  }
}

export function subscribeFixtureStore(listener: FixtureListener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function resetFixtureStore(): void {
  dashboards = cloneFixtureDashboards();
  notify();
}

export function getFixtureDashboard(taskId: string): TaskDashboard | null {
  const dashboard = dashboards.get(taskId);
  return dashboard ? structuredClone(dashboard) : null;
}

function findItem(
  dashboard: TaskDashboard,
  taskItemId: string,
): { item: TaskItemSummary; rowIndex: number; laneIndex: number } | null {
  for (let laneIndex = 0; laneIndex < dashboard.workers.length; laneIndex += 1) {
    const lane = dashboard.workers[laneIndex];
    for (let rowIndex = 0; rowIndex < lane.rows.length; rowIndex += 1) {
      const row = lane.rows[rowIndex];
      if (row.kind === "item" && row.item.id === taskItemId) {
        return { item: row.item, rowIndex, laneIndex };
      }
      if (row.kind === "execution" && row.execution.item?.id === taskItemId) {
        return { item: row.execution.item, rowIndex, laneIndex };
      }
    }
  }
  for (const item of dashboard.inbox_items) {
    if (item.id === taskItemId) {
      return { item, rowIndex: -1, laneIndex: -1 };
    }
  }
  return null;
}

function removeItemRow(dashboard: TaskDashboard, taskItemId: string): void {
  dashboard.inbox_items = dashboard.inbox_items.filter((item) => item.id !== taskItemId);
  for (const lane of dashboard.workers) {
    lane.rows = lane.rows.filter((row) => {
      if (row.kind === "item") return row.item.id !== taskItemId;
      if (row.kind === "execution") return row.execution.task_item_id !== taskItemId;
      return true;
    });
  }
}

export function fixtureStopRunning(taskId: string, taskItemId: string): void {
  const dashboard = dashboards.get(taskId);
  if (!dashboard) return;
  const found = findItem(dashboard, taskItemId);
  if (!found) return;
  found.item.state = "interrupted";
  found.item.updated_at = Math.floor(Date.now() / 1000);
  notify();
}

export function fixtureRemoveItem(taskId: string, taskItemId: string): void {
  const dashboard = dashboards.get(taskId);
  if (!dashboard) return;
  removeItemRow(dashboard, taskItemId);
  notify();
}

export function fixtureRetryItem(taskId: string, taskItemId: string): void {
  const dashboard = dashboards.get(taskId);
  if (!dashboard) return;
  const found = findItem(dashboard, taskItemId);
  if (!found) return;
  found.item.state = "queued";
  found.item.updated_at = Math.floor(Date.now() / 1000);
  notify();
}

export function fixtureUpdateItem(
  taskId: string,
  taskItemId: string,
  patch: Partial<TaskItemSummary>,
): void {
  const dashboard = dashboards.get(taskId);
  if (!dashboard) return;
  const found = findItem(dashboard, taskItemId);
  if (!found) return;
  Object.assign(found.item, patch, { updated_at: Math.floor(Date.now() / 1000) });
  notify();
}

export function fixtureResolveInboxItem(
  taskId: string,
  taskItemId: string,
  resolution: "accept_item" | "reject_item",
): void {
  const dashboard = dashboards.get(taskId);
  if (!dashboard) return;
  if (resolution === "reject_item") {
    dashboard.inbox_items = dashboard.inbox_items.filter((item) => item.id !== taskItemId);
    notify();
    return;
  }
  const item = dashboard.inbox_items.find((row) => row.id === taskItemId);
  if (!item) return;
  item.state = "queued";
  dashboard.inbox_items = dashboard.inbox_items.filter((row) => row.id !== taskItemId);
  const lane = dashboard.workers[0];
  if (lane) {
    lane.rows.unshift({
      kind: "item",
      default_folded: false,
      sort_at: Math.floor(Date.now() / 1000),
      item: { ...item, worker_id: lane.worker_id },
    });
  }
  notify();
}
