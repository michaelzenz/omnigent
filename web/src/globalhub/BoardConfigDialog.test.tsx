import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { BoardConfigButton } from "./BoardConfigDialog";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { BoardRole } from "@/hooks/useGlobalHubRoles";
import { authenticatedFetch } from "@/lib/identity";
import type * as IdentityModule from "@/lib/identity";
import { boardSession, jsonResponse } from "@/test/globalHubFixtures";

vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof IdentityModule>()),
  authenticatedFetch: vi.fn(),
}));

const fetchMock = vi.mocked(authenticatedFetch);
const ROLES_PATH = "/v1/agent-tasks/board/roles";
const ROLES: BoardRole[] = [
  {
    role: "broker",
    title: "Task broker",
    manager_id: null,
    session_id: "s-broker",
    session: boardSession({ status: "running" }),
    waiting: 3,
  },
  {
    role: "custodian",
    title: "Task custodian",
    manager_id: null,
    session_id: null,
    session: null,
    waiting: 0,
  },
  {
    role: "manager:default",
    title: "Storage",
    manager_id: "m1",
    session_id: "s-m1",
    session: boardSession(),
    waiting: 1,
  },
];

async function openDialog(): Promise<void> {
  fetchMock.mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === ROLES_PATH) return jsonResponse({ data: ROLES });
    if (path === "/v1/agent-tasks/managers/m1" && init?.method === "DELETE") {
      return jsonResponse({ id: "m1", conversation_id: "s-m1", deleted: true });
    }
    return jsonResponse({});
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TooltipProvider>
          <BoardConfigButton />
        </TooltipProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Board configuration" }));
  await screen.findByTestId("board-role-broker");
}

/** The requests the dialog's actions sent, in order, without the best-effort stop. */
function actionRequests(): string[] {
  return fetchMock.mock.calls
    .map(([input, init]) => `${init?.method ?? "GET"} ${String(input)}`)
    .filter((request) => !request.endsWith(ROLES_PATH) && !request.endsWith("/events"));
}

async function confirmAction(button: string): Promise<void> {
  fireEvent.click(screen.getByRole("button", { name: button }));
  fireEvent.click(await screen.findByTestId("task-confirm-button"));
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("BoardConfigButton", () => {
  it("lists each role with its session's state, its waiting work and a link", async () => {
    await openDialog();

    const broker = within(screen.getByTestId("board-role-broker"));
    expect(broker.getByTestId("session-state-badge")).toHaveAttribute("data-state", "running");
    expect(broker.getByText("3 waiting")).toBeInTheDocument();
    expect(broker.getByRole("link", { name: "Open the Task broker session" })).toHaveAttribute(
      "href",
      "/c/s-broker",
    );
    const custodian = within(screen.getByTestId("board-role-custodian"));
    expect(custodian.getByText("Custodian · No session")).toBeInTheDocument();
    expect(custodian.queryByRole("link")).toBeNull();
    expect(
      custodian.getByRole("button", { name: "Dismiss the work waiting for Task custodian" }),
    ).toBeDisabled();
  });

  it("resets a role after asking: main deletes its session, then a fresh one starts", async () => {
    await openDialog();

    await confirmAction("Reset Task broker");

    await waitFor(() =>
      expect(actionRequests()).toEqual([
        "DELETE /v1/sessions/s-broker",
        "POST /v1/agent-tasks/roles/broker/session",
      ]),
    );
  });

  it("deletes a manager after asking, then its session", async () => {
    await openDialog();

    await confirmAction("Delete manager Storage");

    await waitFor(() =>
      expect(actionRequests()).toEqual([
        "DELETE /v1/agent-tasks/managers/m1",
        "DELETE /v1/sessions/s-m1",
      ]),
    );
  });

  it("dismisses a manager's waiting events after asking", async () => {
    await openDialog();

    fireEvent.click(screen.getByRole("button", { name: "Dismiss the work waiting for Storage" }));
    expect(
      await screen.findByRole("heading", { name: "Dismiss 1 waiting event for Storage?" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("task-confirm-button"));

    await waitFor(() =>
      expect(actionRequests()).toEqual(["POST /v1/agent-tasks/managers/m1/dismiss-waiting"]),
    );
  });
});
