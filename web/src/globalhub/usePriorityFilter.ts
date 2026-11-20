import { useEffect, useState, useSyncExternalStore } from "react";
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

function savePriorityFilter(key: string | null, priority: BoardPriorityFilter): void {
  if (key === null) return;
  try {
    if (priority === null) localStorage.removeItem(key);
    else localStorage.setItem(key, String(priority));
  } catch {
    // The filter still holds until the page reloads.
  }
}

/**
 * The board's priority filter, kept in this browser for the signed-in user.
 * Until the user is known, a change stays in memory.
 *
 * A search starts at All and does not change the kept filter. When the search
 * ends, a priority picked during it stays; with All picked, the kept filter
 * applies again.
 *
 * @param searching - Whether the board shows a search.
 * @returns The filter, and a function that sets it.
 */
export function usePriorityFilter(
  searching = false,
): [BoardPriorityFilter, (priority: BoardPriorityFilter) => void] {
  const key = useSyncExternalStore(subscribeIdentity, priorityFilterKey, () => null);
  const [saved, setSaved] = useState(() => readPriorityFilter(key));
  const [loadedKey, setLoadedKey] = useState(key);
  if (loadedKey !== key) {
    setLoadedKey(key);
    setSaved(readPriorityFilter(key));
  }
  const [searchPick, setSearchPick] = useState<BoardPriorityFilter>(null);
  const [wasSearching, setWasSearching] = useState(searching);
  if (wasSearching !== searching) {
    setWasSearching(searching);
    setSearchPick(null);
    if (!searching && searchPick !== null) setSaved(searchPick);
  }
  useEffect(() => savePriorityFilter(key, saved), [key, saved]);

  function choose(next: BoardPriorityFilter) {
    if (searching) setSearchPick(next);
    else setSaved(next);
  }

  return [searching ? searchPick : saved, choose];
}
