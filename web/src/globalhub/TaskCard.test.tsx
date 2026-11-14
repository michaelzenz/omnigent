import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { TaskCard } from "./TaskCard";
import type { AgentTask } from "@/hooks/useGlobalHubTasks";
import { authenticatedFetch } from "@/lib/identity";
import type * as IdentityModule from "@/lib/identity";
import { agentTask, jsonResponse } from "@/test/globalHubFixtures";

vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof IdentityModule>()),
  authenticatedFetch: vi.fn(),
}));

const fetchMock = vi.mocked(authenticatedFetch);

function renderCard(task: AgentTask, isLast = false): void {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TaskCard task={task} managerTitle="Storage" isLast={isLast} />
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

  it("shows a resolved task without a manager, and the last card cannot move down", () => {
    renderCard(agentTask("t1", { state: "agent-resolved" }), true);

    expect(screen.getByText("resolved")).toBeInTheDocument();
    expect(screen.getByText("Unmanaged")).toBeInTheDocument();
    expect(screen.queryByTestId("task-card-resolve-t1")).toBeNull();
    expect(screen.getByTestId("task-card-move-to-end-t1")).toBeDisabled();
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

  it("saves an edited goal on Enter", async () => {
    renderCard(agentTask("t1"));

    fireEvent.click(screen.getByTestId("task-card-goal-t1"));
    const input = screen.getByTestId("task-card-goal-input-t1");
    fireEvent.change(input, { target: { value: "Ship the fix" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(await sentRequest()).toEqual(["/v1/agent-tasks/t1", "PATCH", { goal: "Ship the fix" }]);
  });
});
