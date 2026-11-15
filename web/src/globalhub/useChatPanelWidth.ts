import {
  type KeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { globalHubStorageKey } from "@/lib/globalHubStorage";
import { subscribeIdentity } from "@/lib/identity";

export const CHAT_PANEL_MIN_WIDTH_PX = 320;
export const CHAT_PANEL_DEFAULT_WIDTH_PX = 480;
// The board strip left uncovered on the panel's left, where a click closes it.
const BOARD_GAP_PX = 32;
const KEY_STEPS_PX: Record<string, number> = { ArrowLeft: 20, ArrowRight: -20 };

function widthStorageKey(): string | null {
  return globalHubStorageKey("chat-width-px");
}

function readSavedWidth(key: string | null): number {
  if (key === null) return CHAT_PANEL_DEFAULT_WIDTH_PX;
  try {
    const saved = Number(localStorage.getItem(key));
    return Number.isFinite(saved) && saved >= CHAT_PANEL_MIN_WIDTH_PX
      ? saved
      : CHAT_PANEL_DEFAULT_WIDTH_PX;
  } catch {
    return CHAT_PANEL_DEFAULT_WIDTH_PX;
  }
}

function saveWidth(key: string | null, width: number): void {
  if (key === null) return;
  try {
    localStorage.setItem(key, String(width));
  } catch {
    // The width still holds until the page reloads.
  }
}

/**
 * The chat panel's width, set by a drag on its left edge or with the arrow
 * keys, and saved for the signed-in user so that every later open uses it.
 * The panel's parent, the board area, caps the width; CSS caps it again when
 * the window shrinks.
 */
export function useChatPanelWidth() {
  // The signed-in user can become known after the page mounts.
  const storageKey = useSyncExternalStore(subscribeIdentity, widthStorageKey, () => null);
  const [width, setWidth] = useState(() => readSavedWidth(storageKey));
  const [loadedKey, setLoadedKey] = useState(storageKey);
  if (loadedKey !== storageKey) {
    setLoadedKey(storageKey);
    setWidth(readSavedWidth(storageKey));
  }
  const [dragging, setDragging] = useState(false);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const widthRef = useRef(width);
  widthRef.current = width;
  const storageKeyRef = useRef(storageKey);
  storageKeyRef.current = storageKey;

  const clamp = useCallback((next: number) => {
    const area = panelRef.current?.parentElement?.clientWidth;
    const widest = area ? Math.max(CHAT_PANEL_MIN_WIDTH_PX, area - BOARD_GAP_PX) : next;
    return Math.round(Math.max(CHAT_PANEL_MIN_WIDTH_PX, Math.min(next, widest)));
  }, []);

  useEffect(() => {
    if (!dragging) return;
    const move = (event: MouseEvent) => {
      const right = panelRef.current?.getBoundingClientRect().right;
      if (right !== undefined) setWidth(clamp(right - event.clientX));
    };
    const stop = () => {
      setDragging(false);
      saveWidth(storageKeyRef.current, widthRef.current);
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", stop);
    return () => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", stop);
    };
  }, [dragging, clamp]);

  const onKeyDown = (event: KeyboardEvent) => {
    const step = KEY_STEPS_PX[event.key];
    if (step === undefined) return;
    event.preventDefault();
    const next = clamp(widthRef.current + step);
    setWidth(next);
    saveWidth(storageKeyRef.current, next);
  };

  return {
    /** The panel's width in pixels, for its inline style. */
    width,
    /** Attach to the panel, so a drag measures from its right edge. */
    panelRef,
    /** Spread onto the resize handle at the panel's left edge. */
    handleProps: {
      onMouseDown: (event: ReactMouseEvent) => {
        event.preventDefault();
        setDragging(true);
      },
      onKeyDown,
      role: "separator" as const,
      "aria-orientation": "vertical" as const,
      "aria-label": "Resize chat panel",
      tabIndex: 0,
    },
  };
}
