export const DEFAULT_STICKY_USER_MESSAGES = true;
export const STICKY_USER_MESSAGES_STORAGE_KEY = "omnigent:sticky-user-messages";

const CHANGE_EVENT = "omnigent:sticky-user-messages-change";

export function readStickyUserMessagesEnabled(): boolean {
  if (typeof window === "undefined") return DEFAULT_STICKY_USER_MESSAGES;
  try {
    const stored = window.localStorage.getItem(STICKY_USER_MESSAGES_STORAGE_KEY);
    return stored === null ? DEFAULT_STICKY_USER_MESSAGES : stored === "true";
  } catch {
    return DEFAULT_STICKY_USER_MESSAGES;
  }
}

export function writeStickyUserMessagesEnabled(enabled: boolean): void {
  if (typeof window === "undefined") return;
  try {
    if (enabled === DEFAULT_STICKY_USER_MESSAGES) {
      window.localStorage.removeItem(STICKY_USER_MESSAGES_STORAGE_KEY);
    } else {
      window.localStorage.setItem(STICKY_USER_MESSAGES_STORAGE_KEY, String(enabled));
    }
  } catch {
    // localStorage access errors are non-fatal.
  }
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function subscribeStickyUserMessagesEnabled(onChange: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(CHANGE_EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(CHANGE_EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}
