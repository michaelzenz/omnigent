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
    onMoveDown,
  }: {
    isLast?: boolean;
    managerSessionId?: string | null;
    onMoveDown?: (taskId: string) => () => void;
  } = {},
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
              onMoveDown={onMoveDown}
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
    expect(card.getByTestId("task-card-priority-t1")).toHaveTextContent("P1");
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

  it("marks the title when a search finds the task by the meaning of its title", () => {
    const match = { hits: 0, asset_ids: [], worker_ids: [] };
    renderCard(agentTask("t1", { match: { ...match, title_match: true } }));
    renderCard(agentTask("t2", { match }));

    expect(screen.getByTestId("task-card-title-t1")).toHaveClass("ring-amber-400/50");
    expect(screen.getByTestId("task-card-title-t2")).not.toHaveClass("ring-amber-400/50");
  });

  it("shows Has update until a click on the card clears it", async () => {
    renderCard(agentTask("t1", { has_update: true }));
    renderCard(agentTask("t2"));

    expect(within(screen.getByTestId("task-card-t1")).getByText("Has update")).toBeInTheDocument();
    expect(within(screen.getByTestId("task-card-t2")).queryByText("Has update")).toBeNull();
    fireEvent.click(screen.getByTestId("task-card-title-t2"));
    fireEvent.click(screen.getByTestId("task-card-title-t1"));

    expect(await sentRequest()).toEqual(["/v1/agent-tasks/t1", "PATCH", { has_update: false }]);
  });

  it("clears Has update when a button on the card is clicked, and the button still works", async () => {
    renderCard(agentTask("t1", { has_update: true }));

    fireEvent.click(screen.getByTestId("task-card-move-to-end-t1"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(
      fetchMock.mock.calls.map(([path, init]) => `${init?.method} ${String(path)}`).sort(),
    ).toEqual(["PATCH /v1/agent-tasks/t1", "POST /v1/agent-tasks/t1/move-to-queue-end"]);
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

  it.each(["task-card-move-to-end-t1", "task-card-resolve-t1"])(
    "%s tells the board of the move, and undoes that when the move fails",
    async (button) => {
      const undo = vi.fn();
      const onMoveDown = vi.fn(() => undo);
      fetchMock.mockImplementation(async () => jsonResponse({ detail: "unavailable" }, 503));
      renderCard(agentTask("t1"), { onMoveDown });

      fireEvent.click(screen.getByTestId(button));

      expect(onMoveDown).toHaveBeenCalledWith("t1");
      await waitFor(() => expect(undo).toHaveBeenCalledTimes(1));
    },
  );

  it("saves the priority picked from the colored menu", async () => {
    renderCard(agentTask("t1", { priority: 2 }));
    const trigger = screen.getByTestId("task-card-priority-t1");
    expect(trigger).toHaveTextContent("P2");

    fireEvent.pointerDown(trigger, new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
    fireEvent.click(trigger);
    fireEvent.click(await screen.findByTestId("task-card-priority-option-0"));

    expect(await sentRequest()).toEqual(["/v1/agent-tasks/t1", "PATCH", { priority: 0 }]);
  });

  it("asks before it deletes the task permanently", async () => {
    renderCard(agentTask("t1"));

    fireEvent.click(screen.getByTestId("task-card-delete-t1"));
    const sentBeforeConfirming = fetchMock.mock.calls.length;
    fireEvent.click(await screen.findByTestId("task-confirm-button"));

    expect([sentBeforeConfirming, ...(await sentRequest())]).toEqual([
      0,
      "/v1/agent-tasks/t1/permanent",
      "DELETE",
      undefined,
    ]);
  });
});
