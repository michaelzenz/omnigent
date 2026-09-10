import { useMemo } from "react";
import type { AgentTaskSummary } from "@/lib/agentTasksApi";

/**
 * Client-side board search. A task matches when the query (case-insensitive)
 * appears in any of:
 *
 * - list-level fields: title, description, goal, task id
 * - dashboard-level fields for cards whose dashboard is loaded: asset urls +
 *   titles (including workspace paths), worker titles/provider names, worker
 *   session ids (target_id), and task-item titles
 *
 * Dashboard fields are matched only from data already fetched by mounted,
 * visible cards — search never triggers new dashboard fetches. This means a
 * match deep inside an off-screen card's assets appears once that card has
 * been scrolled past (its dashboard is cached) rather than immediately; the
 * list-level fields cover the common case ("find my task by title").
 *
 * Returns the filtered list in the same rank order, plus the normalized
 * query for UI state (empty string when not filtering).
 */
export function filterBoardTasks(
  tasks: AgentTaskSummary[],
  rawQuery: string,
  dashboards: ReadonlyMap<string, unknown>,
): AgentTaskSummary[] {
  const query = rawQuery.trim().toLowerCase();
  if (!query) return tasks;
  return tasks.filter((task) => {
    if (
      task.title?.toLowerCase().includes(query) ||
      task.description?.toLowerCase().includes(query) ||
      task.goal?.toLowerCase().includes(query) ||
      task.id.toLowerCase().includes(query)
    ) {
      return true;
    }
    const dashboard = dashboards.get(task.id);
    if (!dashboard) return false;
    return dashboardMatches(dashboard, query);
  });
}

/** Structural match against an already-loaded TaskDashboard. Untyped here to
 * avoid a circular import with the API module; the shape mirrors it. */
function dashboardMatches(dashboard: unknown, query: string): boolean {
  const d = dashboard as {
    assets?: { title?: string; url?: string | null }[];
    workers?: {
      title?: string | null;
      provider_name?: string | null;
      target_id?: string | null;
      worker_id?: string;
    }[];
    inbox_items?: { title?: string; description?: string | null }[];
    active_items?: { title?: string; description?: string | null }[];
  };
  for (const asset of d.assets ?? []) {
    if (asset.url?.toLowerCase().includes(query)) return true;
    if (asset.title?.toLowerCase().includes(query)) return true;
  }
  for (const worker of d.workers ?? []) {
    if (worker.title?.toLowerCase().includes(query)) return true;
    if (worker.provider_name?.toLowerCase().includes(query)) return true;
    if (worker.target_id?.toLowerCase().includes(query)) return true;
    if (worker.worker_id?.toLowerCase().includes(query)) return true;
  }
  for (const item of [...(d.inbox_items ?? []), ...(d.active_items ?? [])]) {
    if (item.title?.toLowerCase().includes(query)) return true;
    if (item.description?.toLowerCase().includes(query)) return true;
  }
  return false;
}

export function useBoardSearch(
  tasks: AgentTaskSummary[],
  rawQuery: string,
  dashboards: ReadonlyMap<string, unknown>,
): AgentTaskSummary[] {
  return useMemo(
    () => filterBoardTasks(tasks, rawQuery, dashboards),
    [tasks, rawQuery, dashboards],
  );
}
