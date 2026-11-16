import { useState, useSyncExternalStore } from "react";
import { globalHubStorageKey } from "@/lib/globalHubStorage";
import { subscribeIdentity } from "@/lib/identity";

function hiddenSkillsKey(): string | null {
  return globalHubStorageKey("hidden-skills");
}

function readHiddenSkills(key: string | null): string[] {
  if (key === null) return [];
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(key) ?? "[]");
    return Array.isArray(saved)
      ? saved.filter((name): name is string => typeof name === "string")
      : [];
  } catch {
    return [];
  }
}

/**
 * The skill names that the signed-in user hides from the Skills tab, kept in
 * this browser for that user. Until the user is known, changes stay in memory.
 *
 * @returns The hidden names, and a function that hides or shows one name.
 */
export function useHiddenSkills(): [string[], (name: string) => void] {
  // The signed-in user can become known after the page mounts.
  const key = useSyncExternalStore(subscribeIdentity, hiddenSkillsKey, () => null);
  const [hidden, setHidden] = useState(() => readHiddenSkills(key));
  const [loadedKey, setLoadedKey] = useState(key);
  if (loadedKey !== key) {
    setLoadedKey(key);
    setHidden(readHiddenSkills(key));
  }

  function toggle(name: string) {
    setHidden((current) => {
      const next = current.includes(name)
        ? current.filter((item) => item !== name)
        : [...current, name];
      if (key !== null) {
        try {
          localStorage.setItem(key, JSON.stringify(next));
        } catch {
          // The list still holds until the page reloads.
        }
      }
      return next;
    });
  }

  return [hidden, toggle];
}
