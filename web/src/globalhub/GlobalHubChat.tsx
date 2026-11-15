import {
  type ComponentProps,
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";
import { Button } from "@/components/ui/button";

/** A session that the chat panel shows. */
export interface ChatTarget {
  sessionId: string;
  /** Shown until the session loads. */
  title: string;
  kind: "Manager" | "Worker";
}

interface GlobalHubChat {
  /** The session the panel shows, or ``null`` while it is closed. */
  target: ChatTarget | null;
  /** Show ``target``, or close the panel when it already shows that session. */
  toggle: (target: ChatTarget) => void;
  close: () => void;
}

const GlobalHubChatContext = createContext<GlobalHubChat>({
  target: null,
  toggle: () => {},
  close: () => {},
});

/** Holds the session that the Global Hub chat panel shows. */
export function GlobalHubChatProvider({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<ChatTarget | null>(null);
  const toggle = useCallback(
    (next: ChatTarget) =>
      setTarget((current) => (current?.sessionId === next.sessionId ? null : next)),
    [],
  );
  const close = useCallback(() => setTarget(null), []);
  const value = useMemo(() => ({ target, toggle, close }), [target, toggle, close]);
  return <GlobalHubChatContext.Provider value={value}>{children}</GlobalHubChatContext.Provider>;
}

export function useGlobalHubChat(): GlobalHubChat {
  return useContext(GlobalHubChatContext);
}

/** Whether a click on ``target`` lands on a chat button, which switches the panel itself. */
export function isChatOpener(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest("[data-globalhub-chat-opener]") !== null;
}

type ChatButtonProps = { target: ChatTarget; label: string } & Omit<
  ComponentProps<typeof Button>,
  "onClick" | "aria-label" | "aria-pressed"
>;

/** A button that opens ``target`` in the chat panel; it shows as pressed while the panel does. */
export function ChatButton({ target, label, ...buttonProps }: ChatButtonProps) {
  const chat = useGlobalHubChat();
  return (
    <Button
      {...buttonProps}
      data-globalhub-chat-opener=""
      aria-label={label}
      title={label}
      aria-pressed={chat.target?.sessionId === target.sessionId}
      onClick={() => chat.toggle(target)}
    />
  );
}
