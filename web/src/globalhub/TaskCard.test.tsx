import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { GlobalHubChatProvider } from "./GlobalHubChat";
import { TaskCard } from "./TaskCard";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { AgentTaskCard } from "@/hooks/useGlobalHubTasks";
import { authenticatedFetch } from "@/lib/identity";
import type * as IdentityModule from "@/lib/identity";
import { agentTask, jsonResponse, taskWorker } from "@/test/globalHubFixtures";

vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof IdentityModule>()),
  authenticatedFetch: vi.fn(),
}));

const fetchMock = vi.mocked(authenticatedFetch);

function renderCard(
  task: AgentTaskCard,
  {
    isLast = false,
    managerSessionId = "s-m1",
  }: { isLast?: boolean; managerSessionId?: string | null } = {},
): void {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TooltipProvider>
          <GlobalHubChatProvider>
            <TaskCard
              task={task}
              managerTitle="Storage"
              managerSessionId={managerSessionId}
              isLast={isLast}
            />
          </GlobalHubChatProvider>
        </TooltipProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The path, method and parsed body of the only request the card sent. */
async function sentRequest(): Promise<[string, string | undefined, unknown]> {
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  const [path, init] = fetchMock.mock.calls[0];
  const body = init?.body === undefined ? undefined : JSON.parse(String(init.body));
  return [String(path), init?.method, body];
}

beforeEach(() => {
  fetchMock.mockImplementation(async () => jsonResponse({}));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("TaskCard", () => {
  it("shows the task's state, overview and details", async () => {
    const screenshot =
      "https://github.com/user-attachments/assets/381e47c8-e3f1-4c3e-9b3d-aac5cfb3557b";
    renderCard(
      agentTask("t1", {
        manager_id: "m1",
        description: `Retries **fail** on 503.\n\n![screenshot](${screenshot})`,
        priority: 1,
      }),
    );

    const card = within(screen.getByTestId("task-card-t1"));
    expect(await card.findByRole("img", { name: "screenshot" })).toHaveAttribute("src", screenshot);
    expect(card.getByText("fail")).toBeInTheDocument();
    expect(card.queryByText(/\*\*/)).toBeNull();
    expect(card.getByText("active")).toBeInTheDocument();
    expect(card.getByText("Storage")).toBeInTheDocument();
    expect(card.getByText("P1")).toBeInTheDocument();
    expect(card.queryByText("Unmanaged")).toBeNull();
  });

  it.each([
    { session: { status: "running" as const }, spinner: true },
    { session: { background_work_running: true }, spinner: true },
    { session: { status: "running" as const, pending_elicitations_count: 1 }, spinner: false },
    { session: {}, spinner: false },
  ])("counts the workers and spins while one works: $session", ({ session, spinner }) => {
    renderCard(agentTask("t1", { workers: [taskWorker("w1", {}, session), taskWorker("w2")] }));

    expect(screen.queryByRole("img", { name: "Workers running" }) !== null).toBe(spinner);
    expect(screen.getByText("Workers", { selector: "dt" }).nextElementSibling).toHaveTextContent(
      "2",
    );
  });

  it("shows a resolved task without a manager, and the last card cannot move down", () => {
    renderCard(agentTask("t1", { state: "agent-resolved" }), { isLast: true });

    expect(screen.getByText("resolved")).toBeInTheDocument();
    expect(screen.getByText("Unmanaged")).toBeInTheDocument();
    expect(screen.queryByTestId("task-card-resolve-t1")).toBeNull();
    expect(screen.getByTestId("task-card-move-to-end-t1")).toBeDisabled();
  });

  it("opens the manager's chat from its name while the manager has a session", () => {
    renderCard(agentTask("t1", { manager_id: "m1" }));

    const chat = screen.getByRole("button", { name: "Chat with Storage" });
    fireEvent.click(chat);
    expect(chat).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(chat);
    expect(chat).toHaveAttribute("aria-pressed", "false");

    cleanup();
    renderCard(agentTask("t1", { manager_id: "m1" }), { managerSessionId: null });
    expect(screen.getByText("Storage")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Chat with Storage" })).toBeNull();
  });

  it.each([
    {
      button: "task-card-resolve-t1",
      expected: ["/v1/agent-tasks/t1", "PATCH", { state: "agent-resolved" }],
    },
    {
      button: "task-card-move-to-end-t1",
      expected: ["/v1/agent-tasks/t1/move-to-queue-end", "POST", undefined],
    },
  ])("$button sends its request", async ({ button, expected }) => {
    renderCard(agentTask("t1"));

    fireEvent.click(screen.getByTestId(button));

    expect(await sentRequest()).toEqual(expected);
  });

  it.each([
    { button: "task-card-archive-t1", path: "/v1/agent-tasks/t1" },
    { button: "task-card-delete-t1", path: "/v1/agent-tasks/t1/permanent" },
  ])("$button asks before it sends DELETE", async ({ button, path }) => {
    renderCard(agentTask("t1"));

    fireEvent.click(screen.getByTestId(button));
    const sentBeforeConfirming = fetchMock.mock.calls.length;
    fireEvent.click(await screen.findByTestId("task-confirm-button"));

    expect([sentBeforeConfirming, ...(await sentRequest())]).toEqual([
      0,
      path,
      "DELETE",
      undefined,
    ]);
  });
});
