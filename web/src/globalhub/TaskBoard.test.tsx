import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { TaskBoard } from "./TaskBoard";
import type { AgentTask } from "@/hooks/useGlobalHubTasks";
import { authenticatedFetch } from "@/lib/identity";
import type * as IdentityModule from "@/lib/identity";
import { agentTask, jsonResponse } from "@/test/globalHubFixtures";

vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof IdentityModule>()),
  authenticatedFetch: vi.fn(),
}));

const fetchMock = vi.mocked(authenticatedFetch);
const STORAGE_MANAGER = {
  id: "m1",
  title: "Storage",
  conversation_id: null,
  session_missing: false,
};

function serveBoard(byState: Record<"active" | "idle" | "agent-resolved", AgentTask[]>): void {
  const routes: Record<string, unknown> = {
    "/v1/agent-tasks/managers": { data: [STORAGE_MANAGER] },
  };
  for (const [state, tasks] of Object.entries(byState)) {
    routes[`/v1/agent-tasks?state=${state}&limit=100`] = { data: tasks };
  }
  fetchMock.mockImplementation(async (input) => {
    const body = routes[String(input)];
    return body === undefined ? jsonResponse({ detail: "Not found" }, 404) : jsonResponse(body);
  });
}

function renderBoard(): void {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TaskBoard />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("TaskBoard", () => {
  it("lists the live tasks of every state in board order, with their managers' names", async () => {
    serveBoard({
      active: [agentTask("a", { queue_rank: 5, manager_id: "m1" })],
      idle: [agentTask("b", { state: "idle", queue_rank: 9 })],
      "agent-resolved": [agentTask("c", { state: "agent-resolved", queue_rank: 1 })],
    });

    renderBoard();

    const cards = await screen.findAllByTestId(/^task-card-[abc]$/);
    expect(cards.map((card) => card.dataset.testid)).toEqual([
      "task-card-b",
      "task-card-a",
      "task-card-c",
    ]);
    expect(await within(cards[1]).findByText("Storage")).toBeInTheDocument();
  });

  it("says so when no task is live", async () => {
    serveBoard({ active: [], idle: [], "agent-resolved": [] });

    renderBoard();

    expect(await screen.findByTestId("globalhub-no-tasks")).toBeInTheDocument();
  });
});
