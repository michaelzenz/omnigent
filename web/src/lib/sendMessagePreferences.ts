export type SendMessageShortcut = "enter" | "command-enter";

export const DEFAULT_SEND_MESSAGE_SHORTCUT: SendMessageShortcut = "enter";
const STORAGE_KEY = "omnigent:send-message-shortcut";

export function readSendMessageShortcut(): SendMessageShortcut {
  if (typeof window === "undefined") return DEFAULT_SEND_MESSAGE_SHORTCUT;
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "command-enter"
      ? "command-enter"
      : DEFAULT_SEND_MESSAGE_SHORTCUT;
  } catch {
    return DEFAULT_SEND_MESSAGE_SHORTCUT;
  }
}

export function writeSendMessageShortcut(shortcut: SendMessageShortcut): void {
  if (typeof window === "undefined") return;
  try {
    if (shortcut === DEFAULT_SEND_MESSAGE_SHORTCUT) {
      window.localStorage.removeItem(STORAGE_KEY);
    } else {
      window.localStorage.setItem(STORAGE_KEY, shortcut);
    }
  } catch {
    // localStorage access errors are non-fatal.
  }
}

export function isSendMessageShortcut(event: {
  key: string;
  shiftKey: boolean;
  metaKey: boolean;
  ctrlKey: boolean;
}): boolean {
  if (event.key !== "Enter" || event.shiftKey) return false;
  if (readSendMessageShortcut() === "command-enter") {
    return event.metaKey || event.ctrlKey;
  }
  return true;
}
