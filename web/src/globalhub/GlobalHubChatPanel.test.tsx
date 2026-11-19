import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { ChatButton, type ChatTarget, GlobalHubChatProvider } from "./GlobalHubChat";
import { GlobalHubChatPanel } from "./GlobalHubChatPanel";
import type * as IdentityModule from "@/lib/identity";

const identity = vi.hoisted(() => ({ userId: "alice@example.com" }));
vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof IdentityModule>()),
  getCurrentUserId: () => identity.userId,
}));
vi.mock("@/components/chat/SideChatPane", () => ({
  SideChatPane: (props: {
    childId: string;
    fullSession?: boolean;
    queueWhileBusy?: boolean;
    readOnly?: boolean;
    messagePrefix?: string;
  }) => (
    <div
      data-testid="side-chat"
      data-session={props.childId}
      data-full-session={String(props.fullSession)}
      data-queue-while-busy={String(props.queueWhileBusy)}
      data-read-only={String(props.readOnly)}
      data-message-prefix={props.messagePrefix ?? ""}
    />
  ),
}));
vi.mock("@/hooks/useSession", () => ({
  useSession: (id: string) => ({
    session: { id, title: `Session ${id}`, labels: {} },
    isLoading: false,
    error: null,
  }),
}));
vi.mock("@/hooks/usePermissions", () => ({ useCanEdit: () => true }));

const MANAGER: ChatTarget = { sessionId: "s-m1", title: "Storage", kind: "Manager" };
const WORKER: ChatTarget = { sessionId: "s-w1", title: "Fixing retries", kind: "Worker" };

function renderBoard(): void {
  render(
    <MemoryRouter>
      <GlobalHubChatProvider>
        <p>Board</p>
        <ChatButton target={MANAGER} label="Chat with Storage" />
        <ChatButton target={WORKER} label="Chat with Fixing retries" />
        <div className="relative">
          <GlobalHubChatPanel />
        </div>
      </GlobalHubChatProvider>
    </MemoryRouter>,
  );
}

/** Press and click as a pointer does; the panel closes on a press outside it. */
async function press(element: Element): Promise<void> {
  // An open panel starts to watch for presses one task after it opens.
  await act(
    () =>
      new Promise<void>((resolve) => {
        setTimeout(resolve, 0);
      }),
  );
  fireEvent.pointerDown(element);
  fireEvent.click(element);
}

function openChat(label: string): void {
  fireEvent.click(screen.getByRole("button", { name: label }));
}

function panel(): HTMLElement | null {
  return screen.queryByTestId("globalhub-chat-panel");
}

beforeEach(() => {
  localStorage.clear();
  identity.userId = "alice@example.com";
});
afterEach(cleanup);

describe("GlobalHubChatPanel", () => {
  it("shows the whole session with its kind, title and a full-screen link", () => {
    renderBoard();
    openChat("Chat with Storage");

    expect(screen.getByRole("dialog", { name: "Session s-m1" })).toBeInTheDocument();
    expect(screen.getByText("Manager")).toBeInTheDocument();
    const chat = screen.getByTestId("side-chat");
    // A full session shows no settings control, so the panel cannot change the harness.
    expect([
      chat.dataset.session,
      chat.dataset.fullSession,
      chat.dataset.queueWhileBusy,
      chat.dataset.readOnly,
      chat.dataset.messagePrefix,
    ]).toEqual(["s-m1", "true", "true", "false", ""]);
    expect(screen.getByRole("link", { name: "Open Session s-m1 full screen" })).toHaveAttribute(
      "href",
      "/c/s-m1",
    );
  });

  it("switches with one click on another chat button and closes on any other click", async () => {
    renderBoard();

    await press(screen.getByRole("button", { name: "Chat with Storage" }));
    await press(screen.getByRole("button", { name: "Chat with Fixing retries" }));
    expect(screen.getByTestId("side-chat").dataset.session).toBe("s-w1");
    expect(screen.getByRole("button", { name: "Chat with Fixing retries" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await press(screen.getByText("Board"));
    expect(panel()).toBeNull();

    // The open chat's own button closes it.
    await press(screen.getByRole("button", { name: "Chat with Fixing retries" }));
    await press(screen.getByRole("button", { name: "Chat with Fixing retries" }));
    expect(panel()).toBeNull();
  });

  it("starts each message with the task id of the card that opened the manager's chat", async () => {
    render(
      <MemoryRouter>
        <GlobalHubChatProvider>
          <ChatButton target={{ ...MANAGER, taskId: "t-a" }} label="Chat about A" />
          <ChatButton target={{ ...MANAGER, taskId: "t-b" }} label="Chat about B" />
          <div className="relative">
            <GlobalHubChatPanel />
          </div>
        </GlobalHubChatProvider>
      </MemoryRouter>,
    );

    await press(screen.getByRole("button", { name: "Chat about A" }));
    expect(screen.getByTestId("side-chat").dataset.messagePrefix).toBe("taskid: t-a\n");

    await press(screen.getByRole("button", { name: "Chat about B" }));
    expect(screen.getByTestId("side-chat").dataset.messagePrefix).toBe("taskid: t-b\n");
    expect(screen.getByRole("button", { name: "Chat about A" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("keeps each user's resized width for every later open, and never goes below 320 px", () => {
    renderBoard();
    openChat("Chat with Storage");
    const handle = screen.getByRole("separator", { name: "Resize chat panel" });

    fireEvent.keyDown(handle, { key: "ArrowLeft" });
    fireEvent.keyDown(handle, { key: "ArrowLeft" });
    fireEvent.click(screen.getByRole("button", { name: "Close chat" }));
    openChat("Chat with Fixing retries");
    expect(panel()).toHaveStyle({ width: "520px" });

    // A reload keeps the width.
    cleanup();
    renderBoard();
    openChat("Chat with Storage");
    expect(panel()).toHaveStyle({ width: "520px" });

    // Another account in the same browser has its own width.
    cleanup();
    identity.userId = "bob@example.com";
    renderBoard();
    openChat("Chat with Storage");
    expect(panel()).toHaveStyle({ width: "480px" });

    const bobsHandle = screen.getByRole("separator", { name: "Resize chat panel" });
    for (let step = 0; step < 15; step += 1) fireEvent.keyDown(bobsHandle, { key: "ArrowRight" });
    expect(panel()).toHaveStyle({ width: "320px" });
  });
});
