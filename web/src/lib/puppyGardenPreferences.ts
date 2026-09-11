// Per-device preference for the Puppy Garden task board.
//
// Currently a single toggle: whether external sessions discovered by the
// external session watcher should be offered for adoption into tasks. Off by default
// so the adoption flow is opt-in — the broker won't surface adoption cards
// until the user enables it here. Device-local like the other
// `*Preferences` helpers; no account or host state is changed.

const STORAGE_KEY = "omnigent:adopt-external-sessions";

export const DEFAULT_ADOPT_EXTERNAL_SESSIONS = false;

/**
 * Read the persisted "adopt external sessions" preference. Returns the
 * default (off) when nothing is stored, on a server render (no `window`), or
 * when the stored value is malformed — never throws.
 */
export function readAdoptExternalSessions(): boolean {
  if (typeof window === "undefined") return DEFAULT_ADOPT_EXTERNAL_SESSIONS;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === null) return DEFAULT_ADOPT_EXTERNAL_SESSIONS;
    return raw === "true";
  } catch {
    return DEFAULT_ADOPT_EXTERNAL_SESSIONS;
  }
}

/**
 * Persist the "adopt external sessions" preference. Swallows quota/access
 * errors so a failed write can't break the app.
 */
export function writeAdoptExternalSessions(value: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, value ? "true" : "false");
  } catch {
    // localStorage quota or access errors shouldn't break the app.
  }
}

// Default desktop app for opening ``kind=workspace`` task assets. Like the
// other PuppyGarden preferences this is device-local (localStorage); the
// editor still must be detected by the desktop shell at launch time.
export type WorkspaceEditor = "vscode" | "cursor";

const EDITOR_STORAGE_KEY = "omnigent:workspace-editor";
export const DEFAULT_WORKSPACE_EDITOR: WorkspaceEditor = "vscode";

/** Read the persisted default workspace editor. */
export function readWorkspaceEditor(): WorkspaceEditor {
  if (typeof window === "undefined") return DEFAULT_WORKSPACE_EDITOR;
  try {
    const raw = window.localStorage.getItem(EDITOR_STORAGE_KEY);
    return raw === "cursor" ? "cursor" : DEFAULT_WORKSPACE_EDITOR;
  } catch {
    return DEFAULT_WORKSPACE_EDITOR;
  }
}

/** Persist the default workspace editor. Swallows storage errors. */
export function writeWorkspaceEditor(value: WorkspaceEditor): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(EDITOR_STORAGE_KEY, value);
  } catch {
    // localStorage quota or access errors shouldn't break the app.
  }
}
