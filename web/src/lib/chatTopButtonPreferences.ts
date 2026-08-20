export type ChatTopButtonMode = "jump-to-top" | "jump-to-last-message" | "off";

export const DEFAULT_CHAT_TOP_BUTTON_MODE: ChatTopButtonMode = "jump-to-top";
export const CHAT_TOP_BUTTON_STORAGE_KEY = "omnigent:chat-top-button";

const CHANGE_EVENT = "omnigent:chat-top-button-change";

function isChatTopButtonMode(value: string | null): value is ChatTopButtonMode {
  return value === "jump-to-top" || value === "jump-to-last-message" || value === "off";
}

export function readChatTopButtonMode(): ChatTopButtonMode {
  if (typeof window === "undefined") return DEFAULT_CHAT_TOP_BUTTON_MODE;
  try {
    const stored = window.localStorage.getItem(CHAT_TOP_BUTTON_STORAGE_KEY);
    return isChatTopButtonMode(stored) ? stored : DEFAULT_CHAT_TOP_BUTTON_MODE;
  } catch {
    return DEFAULT_CHAT_TOP_BUTTON_MODE;
  }
}

export function writeChatTopButtonMode(mode: ChatTopButtonMode): void {
  if (typeof window === "undefined") return;
  try {
    if (mode === DEFAULT_CHAT_TOP_BUTTON_MODE) {
      window.localStorage.removeItem(CHAT_TOP_BUTTON_STORAGE_KEY);
    } else {
      window.localStorage.setItem(CHAT_TOP_BUTTON_STORAGE_KEY, mode);
    }
  } catch {
    // localStorage access errors are non-fatal.
  }
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function subscribeChatTopButtonMode(onChange: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(CHANGE_EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(CHANGE_EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}
