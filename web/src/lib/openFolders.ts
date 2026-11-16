import type { OpenFolder, UiOpenFoldersEvent } from "./events";

/** The stamped folders in a payload, or null when it is not a list of titled paths. */
export function parseOpenFolders(value: unknown): OpenFolder[] | null {
  if (!Array.isArray(value)) return null;
  const folders: OpenFolder[] = [];
  for (const entry of value) {
    if (typeof entry !== "object" || entry === null) return null;
    const { title, path } = entry as Record<string, unknown>;
    if (typeof title !== "string" || typeof path !== "string" || !title || !path) return null;
    folders.push({ title, path });
  }
  return folders;
}

type Listener = (event: UiOpenFoldersEvent, conversationId: string | null) => void;
const listeners = new Set<Listener>();

/** Stamps arrive on the session stream; the folder list route has the stored list. */
export function onUiOpenFolders(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function emitUiOpenFolders(event: UiOpenFoldersEvent, conversationId: string | null): void {
  for (const listener of listeners) {
    try {
      listener(event, conversationId);
    } catch (error) {
      console.warn("[ui-open-folders] listener failed:", error);
    }
  }
}
