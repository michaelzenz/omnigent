import { useMemo } from "react";
import type { AgentTaskSummary, TaskWorkerLane, TaskWorkerRow } from "@/lib/agentTasksApi";

/**
 * Client-side board search. A task matches when the query (case-insensitive)
 * appears in any of:
 *
 * - list-level fields: title, description, goal, task id
 * - dashboard-level fields for cards whose dashboard is loaded: asset urls +
 *   titles (including workspace paths), worker titles/provider names, session
 *   ids (worker target_id, execution conversation ids, manager conversation),
 *   worker situation/failure text, and session history (task items and
 *   execution results/errors across worker rows)
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

// ---- Shared searchable-text collectors (used by the filter and by the
// per-element highlight rings in the card components) ----

export function assetTexts(asset: { title?: string; url?: string | null }): string[] {
  return [asset.title, asset.url ?? null].filter((t): t is string => Boolean(t));
}

/** Every string a worker lane contributes to search: label, session ids,
 * situation text, and its full session history (rows). */
export function laneTexts(lane: TaskWorkerLane): string[] {
  return [
    lane.title ?? null,
    lane.provider_name ?? null,
    lane.worker_id,
    lane.target_id ?? null,
    lane.situation,
    lane.failure_reason ?? null,
    ...lane.rows.flatMap(rowTexts),
  ].filter((t): t is string => Boolean(t));
}

/** Searchable text for one worker-history row (task item or execution). */
export function rowTexts(row: TaskWorkerRow): string[] {
  if (row.kind === "item") {
    const item = row.item;
    return [item.title, item.description, item.instructions].filter((t): t is string => Boolean(t));
  }
  const execution = row.execution;
  return [
    execution.event_title,
    execution.item?.title ?? null,
    execution.result_summary,
    execution.error,
    execution.conversation_id,
  ].filter((t): t is string => Boolean(t));
}

function itemTexts(item: {
  title?: string;
  description?: string | null;
  instructions?: string | null;
}): string[] {
  return [item.title ?? null, item.description ?? null, item.instructions ?? null].filter(
    (t): t is string => Boolean(t),
  );
}

function matchesAny(texts: string[], query: string): boolean {
  return texts.some((text) => text.toLowerCase().includes(query));
}

/** Structural match against an already-loaded TaskDashboard. Untyped here to
 * avoid a circular import with the API module; the shape mirrors it. */
function dashboardMatches(dashboard: unknown, query: string): boolean {
  const d = dashboard as {
    task?: { manager_conversation_id?: string | null };
    assets?: { title?: string; url?: string | null }[];
    workers?: TaskWorkerLane[];
    inbox_items?: { title?: string; description?: string | null; instructions?: string | null }[];
    active_items?: { title?: string; description?: string | null; instructions?: string | null }[];
    recent_done_items?: { all?: { title?: string; description?: string | null }[] };
  };
  if (d.task?.manager_conversation_id?.toLowerCase().includes(query)) return true;
  for (const asset of d.assets ?? []) {
    if (matchesAny(assetTexts(asset), query)) return true;
  }
  for (const worker of d.workers ?? []) {
    if (matchesAny(laneTexts(worker), query)) return true;
  }
  for (const item of [...(d.inbox_items ?? []), ...(d.active_items ?? [])]) {
    if (matchesAny(itemTexts(item), query)) return true;
  }
  for (const item of d.recent_done_items?.all ?? []) {
    if (matchesAny(itemTexts(item), query)) return true;
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
