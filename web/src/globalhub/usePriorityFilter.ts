import { useState, useSyncExternalStore } from "react";
import { globalHubStorageKey } from "@/lib/globalHubStorage";
import { subscribeIdentity } from "@/lib/identity";

/** The one priority that the board shows, from 0 (P0) to 3 (P3), or ``null`` for every task. */
export type BoardPriorityFilter = number | null;

function priorityFilterKey(): string | null {
  return globalHubStorageKey("priority-filter");
}

function readPriorityFilter(key: string | null): BoardPriorityFilter {
  if (key === null) return null;
  try {
    const saved = localStorage.getItem(key);
    return saved !== null && /^[0-3]$/.test(saved) ? Number(saved) : null;
  } catch {
    return null;
  }
}

/**
 * The board's priority filter, kept in this browser for the signed-in user.
 * Until the user is known, a change stays in memory.
 *
 * @returns The filter, and a function that sets it.
 */
export function usePriorityFilter(): [
  BoardPriorityFilter,
  (priority: BoardPriorityFilter) => void,
] {
  const key = useSyncExternalStore(subscribeIdentity, priorityFilterKey, () => null);
  const [priority, setPriority] = useState(() => readPriorityFilter(key));
  const [loadedKey, setLoadedKey] = useState(key);
  if (loadedKey !== key) {
    setLoadedKey(key);
    setPriority(readPriorityFilter(key));
  }

  function choose(next: BoardPriorityFilter) {
    setPriority(next);
    if (key === null) return;
    try {
      if (next === null) localStorage.removeItem(key);
      else localStorage.setItem(key, String(next));
    } catch {
      // The filter still holds until the page reloads.
    }
  }

  return [priority, choose];
}
