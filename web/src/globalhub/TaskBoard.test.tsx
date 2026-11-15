import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { TaskBoard } from "./TaskBoard";
import type { AgentTaskBoard } from "@/hooks/useGlobalHubTasks";
import { authenticatedFetch } from "@/lib/identity";
import type * as IdentityModule from "@/lib/identity";
import { agentTask, jsonResponse } from "@/test/globalHubFixtures";

vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof IdentityModule>()),
  authenticatedFetch: vi.fn(),
}));

const fetchMock = vi.mocked(authenticatedFetch);
const BOARD_PATH = "/v1/agent-tasks/board?limit=";
const SPAWN_PATH = "/v1/agent-tasks/spawn-managers";
const STORAGE_MANAGER = {
  id: "m1",
  title: "Storage",
  conversation_id: null,
  session_missing: false,
};

/** Serve the managers, the board read for each window size, and requests for managers. */
function serveBoard(read: (limit: number) => AgentTaskBoard): void {
  fetchMock.mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/v1/agent-tasks/managers") return jsonResponse({ data: [STORAGE_MANAGER] });
    if (path.startsWith(BOARD_PATH)) {
      return jsonResponse(read(Number(path.slice(BOARD_PATH.length))));
    }
    if (path === SPAWN_PATH) return jsonResponse({ unmanaged_count: 2, queued: true });
    return jsonResponse({ detail: "Not found" }, 404);
  });
}

/** The window sizes the board read, in order. */
function readLimits(): number[] {
  return fetchMock.mock.calls
    .map(([input]) => String(input))
    .filter((path) => path.startsWith(BOARD_PATH))
    .map((path) => Number(path.slice(BOARD_PATH.length)));
}

function renderBoard(): QueryClient {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TaskBoard scrollRoot={{ current: null }} />
    </QueryClientProvider>,
  );
  return client;
}

/** Reports every observed element as in view, as when the end of the list is on screen. */
class InViewObserver {
  private readonly report: IntersectionObserverCallback;
  private connected = true;

  constructor(report: IntersectionObserverCallback) {
    this.report = report;
  }

  observe(target: Element): void {
    queueMicrotask(() => {
      if (!this.connected) return;
      const entry = { isIntersecting: true, target } as IntersectionObserverEntry;
      this.report([entry], this as unknown as IntersectionObserver);
    });
  }

  unobserve(): void {}

  disconnect(): void {
    this.connected = false;
  }

  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("TaskBoard", () => {
  it("shows the cards in board order with their managers' names", async () => {
    serveBoard(() => ({
      data: [agentTask("b", { manager_id: "m1" }), agentTask("a"), agentTask("c")],
      has_more: false,
      unmanaged_count: 0,
    }));

    renderBoard();

    const cards = await screen.findAllByTestId(/^task-card-[abc]$/);
    expect(cards.map((card) => card.dataset.testid)).toEqual([
      "task-card-b",
      "task-card-a",
      "task-card-c",
    ]);
    expect(await within(cards[0]).findByText("Storage")).toBeInTheDocument();
    expect(screen.getByTestId("task-card-move-to-end-c")).toBeDisabled();
    expect(screen.queryByTestId("globalhub-unmanaged")).toBeNull();
    expect(readLimits()).toEqual([20]);
  });

  it("asks the broker for managers once, until the number of unmanaged tasks changes", async () => {
    let unmanaged = 2;
    serveBoard(() => ({ data: [agentTask("a")], has_more: false, unmanaged_count: unmanaged }));
    const client = renderBoard();

    fireEvent.click(await screen.findByRole("button", { name: "Ask the broker to assign them" }));

    expect(await screen.findByText("Sent to the broker.")).toBeInTheDocument();
    expect(screen.getByText("2 tasks have no manager.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask the broker to assign them" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledWith(SPAWN_PATH, { method: "POST" });

    unmanaged = 1;
    await act(() => client.invalidateQueries());

    expect(await screen.findByText("1 task has no manager.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask the broker to assign it" })).toBeEnabled();
    expect(screen.queryByText("Sent to the broker.")).toBeNull();
  });

  it("reads 20 more cards each time the end of the list is in view, until no more are left", async () => {
    vi.stubGlobal("IntersectionObserver", InViewObserver);
    serveBoard((limit) => ({ data: [agentTask("t1")], has_more: limit < 100, unmanaged_count: 0 }));

    renderBoard();

    await waitFor(() => expect(readLimits()).toEqual([20, 40, 60, 80, 100]));
    // The board's last card is loaded once no more are left.
    await waitFor(() => expect(screen.getByTestId("task-card-move-to-end-t1")).toBeDisabled());
  });

  it("says so when no task is live", async () => {
    serveBoard(() => ({ data: [], has_more: false, unmanaged_count: 0 }));

    renderBoard();

    expect(await screen.findByTestId("globalhub-no-tasks")).toBeInTheDocument();
  });
});
