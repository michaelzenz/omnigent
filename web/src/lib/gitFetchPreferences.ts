// Browser-local Git preference controlling whether worktree creation may
// fetch when the requested base ref is unavailable locally.

const STORAGE_KEY = "omnigent:auto-fetch-worktree-base";

export const DEFAULT_AUTO_FETCH_WORKTREE_BASE = false;

/** Read the preference, defaulting off when absent or inaccessible. */
export function readAutoFetchWorktreeBase(): boolean {
  if (typeof window === "undefined") return DEFAULT_AUTO_FETCH_WORKTREE_BASE;
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "true";
  } catch {
    return DEFAULT_AUTO_FETCH_WORKTREE_BASE;
  }
}

/** Persist whether worktree creation may fetch and retry a missing base ref. */
export function writeAutoFetchWorktreeBase(enabled: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, String(enabled));
  } catch {
    // localStorage quota or access errors shouldn't break settings.
  }
}
