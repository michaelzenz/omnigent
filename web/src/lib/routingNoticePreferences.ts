export const DEFAULT_ROUTING_NOTICES_ENABLED = true;
export const ROUTING_NOTICES_STORAGE_KEY = "omnigent:routing-notices";

const CHANGE_EVENT = "omnigent:routing-notices-change";

export function readRoutingNoticesEnabled(): boolean {
  if (typeof window === "undefined") return DEFAULT_ROUTING_NOTICES_ENABLED;
  try {
    const stored = window.localStorage.getItem(ROUTING_NOTICES_STORAGE_KEY);
    return stored === null ? DEFAULT_ROUTING_NOTICES_ENABLED : stored === "true";
  } catch {
    return DEFAULT_ROUTING_NOTICES_ENABLED;
  }
}

export function writeRoutingNoticesEnabled(enabled: boolean): void {
  if (typeof window === "undefined") return;
  try {
    if (enabled === DEFAULT_ROUTING_NOTICES_ENABLED) {
      window.localStorage.removeItem(ROUTING_NOTICES_STORAGE_KEY);
    } else {
      window.localStorage.setItem(ROUTING_NOTICES_STORAGE_KEY, String(enabled));
    }
  } catch {
    // localStorage access errors are non-fatal.
  }
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function subscribeRoutingNoticesEnabled(onChange: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(CHANGE_EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(CHANGE_EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}
