import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { GlobalHubChatProvider } from "./GlobalHubChat";
import { TaskBoard } from "./TaskBoard";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { AgentTaskBoard } from "@/hooks/useGlobalHubTasks";
import { resetReadStateForTests } from "@/hooks/useUnseenConversations";
import { authenticatedFetch } from "@/lib/identity";
import type * as IdentityModule from "@/lib/identity";
import { agentTask, jsonResponse, taskWorker } from "@/test/globalHubFixtures";

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

interface BoardRead {
  limit: number;
  query: string | null;
  after: string | null;
  before: string | null;
}

function readOf(path: string): BoardRead {
  const params = new URL(path, "http://board.example.com").searchParams;
  return {
    limit: Number(params.get("limit")),
    query: params.get("q"),
    after: params.get("after"),
    before: params.get("before"),
  };
}

/** The board reads the board sent, each with its page size, search and cursors. */
function boardReads(): BoardRead[] {
  return fetchMock.mock.calls
    .map(([input]) => String(input))
    .filter((path) => path.startsWith(BOARD_PATH))
    .map(readOf);
}

/** Serve the managers, the board page for each read, and requests for managers. */
function serveBoard(page: (read: BoardRead) => AgentTaskBoard): void {
  fetchMock.mockImplementation(async (input) => {
    const path = String(input);
    if (path.startsWith("/v1/agent-tasks/managers?include_full=true")) {
      return jsonResponse({ data: [STORAGE_MANAGER] });
    }
    if (path.startsWith(BOARD_PATH)) return jsonResponse(page(readOf(path)));
    if (path === SPAWN_PATH) return jsonResponse({ unmanaged_count: 2, queued: true });
    return jsonResponse({ detail: "Not found" }, 404);
  });
}

function renderBoard(query?: string, priority: number | null = null): QueryClient {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TooltipProvider>
          <GlobalHubChatProvider>
            <TaskBoard scrollRoot={{ current: null }} query={query} priority={priority} />
          </GlobalHubChatProvider>
        </TooltipProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return client;
}

/**
 * Reports every observed element as in view, as when the end of the list is on
 * screen. As in a browser, the report comes in a later task.
 */
class InViewObserver {
  private readonly report: IntersectionObserverCallback;
  private connected = true;

  constructor(report: IntersectionObserverCallback) {
    this.report = report;
  }

  observe(target: Element): void {
    setTimeout(() => {
      if (!this.connected) return;
      const entry = { isIntersecting: true, target } as IntersectionObserverEntry;
      this.report([entry], this as unknown as IntersectionObserver);
    }, 0);
  }

  unobserve(): void {}

  disconnect(): void {
    this.connected = false;
  }

  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }
}

/** Reports no element in view, so a page loads only when "Load more" is clicked. */
class OutOfViewObserver {
  observe(): void {}

  unobserve(): void {}

  disconnect(): void {}

  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }
}

/** Clicks the lowest "Load more" once for each page from ``first`` to ``last``, in order. */
async function loadPagesBelow(first: number, last: number): Promise<void> {
  if (first > last) return;
  const loadMore = screen.getAllByRole("button", { name: "Load more" });
  fireEvent.click(loadMore[loadMore.length - 1]);
  await screen.findByTestId(`task-card-t${first}`);
  await loadPagesBelow(first + 1, last);
}

/** The tasks the board shows, top to bottom. */
function shownTasks(): string[] {
  return screen
    .queryAllByTestId(/^task-card-t\d$/)
    .map((card) => (card.dataset.testid ?? "").replace("task-card-", ""));
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  resetReadStateForTests();
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
    expect(boardReads()).toEqual([{ limit: 20, query: null, after: null, before: null }]);
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

  it("reads one more page each time the end of the list is in view, and shows a task two pages send once", async () => {
    vi.stubGlobal("IntersectionObserver", InViewObserver);
    serveBoard(({ after }) => {
      const page = after === null ? 1 : Number(after.replace("page-", "")) + 1;
      return {
        data: page === 2 ? [agentTask("t1"), agentTask("t2")] : [agentTask(`t${page}`)],
        has_more: page < 5,
        next_cursor: page < 5 ? `page-${page}` : null,
        unmanaged_count: 0,
      };
    });

    renderBoard();

    await waitFor(() =>
      expect(boardReads().map((read) => read.after)).toEqual([
        null,
        "page-1",
        "page-2",
        "page-3",
        "page-4",
      ]),
    );
    // The board's last card is loaded once no more are left.
    await waitFor(() => expect(screen.getByTestId("task-card-move-to-end-t5")).toBeDisabled());
    expect(screen.getAllByTestId(/^task-card-t\d$/).map((card) => card.dataset.testid)).toEqual([
      "task-card-t1",
      "task-card-t2",
      "task-card-t3",
      "task-card-t4",
      "task-card-t5",
    ]);
  });

  it("holds five pages, and reads a dropped page again from the top of the list", async () => {
    vi.stubGlobal("IntersectionObserver", OutOfViewObserver);
    serveBoard(({ after, before }) => {
      let page = 1;
      if (after) page = Number(after.replace("page-", "")) + 1;
      if (before) page = Number(before.replace("page-", "")) - 1;
      return {
        data: [agentTask(`t${page}`)],
        has_more: page < 7,
        next_cursor: page < 7 ? `page-${page}` : null,
        prev_cursor: page > 1 ? `page-${page}` : null,
        unmanaged_count: 0,
      };
    });
    renderBoard();
    await screen.findByTestId("task-card-t1");

    await loadPagesBelow(2, 7);

    expect(shownTasks()).toEqual(["t3", "t4", "t5", "t6", "t7"]);
    expect(screen.getByTestId("task-card-move-to-end-t7")).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Load more" }));

    await waitFor(() => expect(shownTasks()).toEqual(["t2", "t3", "t4", "t5", "t6"]));
    expect(boardReads().at(-1)).toEqual({ limit: 20, query: null, after: null, before: "page-3" });
  });

  it("reads the matching cards for a search, and says so when none match", async () => {
    serveBoard(({ query }) => ({
      data:
        query === "retries"
          ? [agentTask("t1", { match: { hits: 1, asset_ids: [], worker_ids: [] } })]
          : [],
      has_more: false,
      unmanaged_count: 0,
    }));

    renderBoard("retries");

    expect(await screen.findByTestId("task-card-t1")).toBeInTheDocument();
    expect(boardReads()).toEqual([{ limit: 20, query: "retries", after: null, before: null }]);
    // A search result's place on the board is not known, so it can still move down.
    expect(screen.getByTestId("task-card-move-to-end-t1")).toBeEnabled();

    cleanup();
    renderBoard("nothing");

    expect(await screen.findByTestId("globalhub-no-matches")).toHaveTextContent(
      'No tasks match "nothing".',
    );
  });

  it("marks a worker with new messages until the chat panel shows its session", async () => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    // The user last saw the session before its latest turn finished.
    const worker = taskWorker("w1", {}, { updated_at: 200, viewer_last_seen: 100 });
    serveBoard(() => ({
      data: [agentTask("t1", { workers: [worker] })],
      has_more: false,
      unmanaged_count: 0,
    }));
    renderBoard();
    const rail = within(await screen.findByTestId("task-card-rail-t1"));
    const tab = rail.getByRole("tab", { name: /Workers/ });
    expect(await within(tab).findByRole("img", { name: "New messages" })).toBeInTheDocument();

    // Radix selects a tab on focus; jsdom's click does not move focus.
    fireEvent.focus(tab);
    fireEvent.click(tab);
    const chat = rail.getByRole("button", { name: "Chat with Session w1" });
    fireEvent.click(chat);
    fireEvent.click(chat);

    // The panel is closed again, and the session stays read.
    expect(rail.queryAllByRole("img", { name: "New messages" })).toEqual([]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/sessions/s-w1/read-state",
      expect.objectContaining({ method: "PUT" }),
    );
  });

  it.each([
    { priority: null, text: "No tasks yet.", sent: null },
    { priority: 1, text: "No P1 tasks.", sent: "1" },
  ])(
    "reads the tasks of priority $priority, and says so when none is live",
    async ({ priority, text, sent }) => {
      serveBoard(() => ({ data: [], has_more: false, unmanaged_count: 0 }));

      renderBoard(undefined, priority);

      expect(await screen.findByTestId("globalhub-no-tasks")).toHaveTextContent(text);
      const priorities = fetchMock.mock.calls
        .map(([input]) => String(input))
        .filter((path) => path.startsWith(BOARD_PATH))
        .map((path) => new URL(path, "http://board.example.com").searchParams.get("priority"));
      expect(priorities).toEqual([sent]);
    },
  );

  it("lets the last card of a filtered board move down, since the board goes on past it", async () => {
    serveBoard(() => ({
      data: [agentTask("t1", { priority: 1 })],
      has_more: false,
      unmanaged_count: 0,
    }));

    renderBoard(undefined, 1);

    expect(await screen.findByTestId("task-card-move-to-end-t1")).toBeEnabled();
  });
});
