import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { GlobalHubChatProvider } from "./GlobalHubChat";
import { TaskCardRail } from "./TaskCardRail";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { AgentTaskCard } from "@/hooks/useGlobalHubTasks";
import {
  markConversationRead,
  resetReadStateForTests,
  seedReadState,
} from "@/hooks/useUnseenConversations";
import { authenticatedFetch } from "@/lib/identity";
import type * as IdentityModule from "@/lib/identity";
import { agentTask, jsonResponse, taskAsset, taskWorker } from "@/test/globalHubFixtures";

vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof IdentityModule>()),
  authenticatedFetch: vi.fn(),
}));

const fetchMock = vi.mocked(authenticatedFetch);

function renderRail(...tasks: AgentTaskCard[]): void {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TooltipProvider>
          <GlobalHubChatProvider>
            {tasks.map((task) => (
              <TaskCardRail key={task.id} task={task} />
            ))}
          </GlobalHubChatProvider>
        </TooltipProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function openWorkersTab(scope: Pick<typeof screen, "getByRole"> = screen): void {
  // Radix selects a tab on focus; jsdom's click does not move focus.
  const tab = scope.getByRole("tab", { name: /Workers/ });
  fireEvent.focus(tab);
  fireEvent.click(tab);
}

/** The path and method of the only request the panel sent. */
async function sentRequest(): Promise<[string, string | undefined]> {
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  const [path, init] = fetchMock.mock.calls[0];
  return [String(path), init?.method];
}

beforeEach(() => {
  fetchMock.mockImplementation(async () => jsonResponse({}));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  resetReadStateForTests();
});

describe("TaskCardRail", () => {
  it("counts both tabs and lists the assets by category, each a link to its URL", () => {
    renderRail(
      agentTask("t1", {
        assets: [
          taskAsset("a1", { category: "runbooks", title: "Retry runbook" }),
          taskAsset("a2", { category: "code", title: "Fix PR", url: "https://example.com/pull/1" }),
        ],
        workers: [taskWorker("w1")],
      }),
    );

    expect(screen.getByRole("tab", { name: "Assets (2)" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Workers (1)" })).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 4 }).map((h) => h.textContent)).toEqual([
      "Code",
      "runbooks",
    ]);
    const link = screen.getByRole("link", { name: "Fix PR" });
    expect([link.getAttribute("href"), link.getAttribute("target")]).toEqual([
      "https://example.com/pull/1",
      "_blank",
    ]);
  });

  it("removes an asset without asking", async () => {
    renderRail(agentTask("t1", { assets: [taskAsset("a1", { title: "Design doc" })] }));

    fireEvent.click(screen.getByRole("button", { name: "Remove Design doc" }));

    expect(await sentRequest()).toEqual(["/v1/agent-tasks/t1/assets/a1", "DELETE"]);
  });

  it("unfolds the workers that worked on an asset, and says when none is left", () => {
    renderRail(
      agentTask("t1", {
        assets: [
          taskAsset("a1", { related_session_ids: ["s-w1", "s-gone"] }),
          taskAsset("a2", { related_session_ids: ["s-gone"] }),
        ],
        workers: [taskWorker("w1", { title: "Fixing retries" })],
      }),
    );

    const withWorker = within(screen.getByTestId("task-asset-a1"));
    fireEvent.click(withWorker.getByRole("button", { name: "1 session" }));

    expect(withWorker.getByRole("link", { name: "Open Fixing retries" })).toHaveAttribute(
      "href",
      "/c/s-w1",
    );
    expect(
      within(screen.getByTestId("task-asset-a2")).getByText("all related workers removed"),
    ).toBeInTheDocument();
  });

  it("shows each worker's session state, a chat button and a link to its session", () => {
    renderRail(
      agentTask("t1", {
        workers: [
          taskWorker("w1", { title: "Fixing retries" }, { pending_elicitations_count: 1 }),
          taskWorker("w2", {}, { title: null }),
        ],
      }),
    );

    openWorkersTab();

    const waiting = within(screen.getByTestId("task-worker-w1"));
    expect(waiting.getByText("Needs response")).toBeInTheDocument();
    expect(waiting.getByRole("link", { name: "Open Fixing retries" })).toHaveAttribute(
      "href",
      "/c/s-w1",
    );
    const chat = waiting.getByRole("button", { name: "Chat with Fixing retries" });
    fireEvent.click(chat);
    expect(chat).toHaveAttribute("aria-pressed", "true");
    const idle = within(screen.getByTestId("task-worker-w2"));
    expect(idle.getByText("Untitled session")).toBeInTheDocument();
    expect(idle.queryByTestId("session-state-badge")).toBeNull();
  });

  it("marks each worker on a session with new messages and its tab, until the session is read", () => {
    seedReadState([{ id: "s-shared", viewer_last_seen: 100 }]);
    const onShared = (id: string) =>
      taskWorker(id, { session_id: "s-shared" }, { updated_at: 200 });
    renderRail(
      agentTask("t1", { workers: [onShared("w1")] }),
      agentTask("t2", { workers: [onShared("w2"), taskWorker("w3")] }),
    );
    openWorkersTab(within(screen.getByTestId("task-card-rail-t2")));
    const dots = (testId: string) =>
      within(screen.getByTestId(testId)).queryAllByRole("img", { name: "New messages" }).length;

    // The first rail's dot is on its tab; the second rail shows the tab and the row.
    expect(["task-card-rail-t1", "task-worker-w2", "task-worker-w3"].map(dots)).toEqual([1, 1, 0]);

    act(() => markConversationRead("s-shared", 200));

    expect(screen.queryAllByRole("img", { name: "New messages" })).toEqual([]);
  });

  it("outlines what a search matched, and opens on the workers when only they match", () => {
    const workers = [taskWorker("w1", { title: "Fixing retries" }), taskWorker("w2")];
    renderRail(
      agentTask("t1", {
        assets: [taskAsset("a1")],
        workers,
        match: { hits: 1, asset_ids: [], worker_ids: ["w1"] },
      }),
    );

    expect(screen.getByRole("tab", { name: /Workers/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("task-worker-w1")).toHaveClass("ring-amber-400/50");
    expect(screen.getByTestId("task-worker-w2")).not.toHaveClass("ring-amber-400/50");

    cleanup();
    renderRail(
      agentTask("t1", {
        assets: [taskAsset("a1"), taskAsset("a2")],
        workers,
        match: { hits: 1, asset_ids: ["a2"], worker_ids: ["w1"] },
      }),
    );

    expect(screen.getByRole("tab", { name: /Assets/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("task-asset-a2")).toHaveClass("ring-amber-400/50");
    expect(screen.getByTestId("task-asset-a1")).not.toHaveClass("ring-amber-400/50");
  });

  it("untracks a worker after asking", async () => {
    renderRail(agentTask("t1", { workers: [taskWorker("w1", { title: "Fixing retries" })] }));
    openWorkersTab();

    fireEvent.click(screen.getByRole("button", { name: "Untrack Fixing retries" }));
    const sentBeforeConfirming = fetchMock.mock.calls.length;
    fireEvent.click(await screen.findByTestId("task-confirm-button"));

    expect([sentBeforeConfirming, ...(await sentRequest())]).toEqual([
      0,
      "/v1/task-workers/w1/untrack",
      "POST",
    ]);
  });
});
