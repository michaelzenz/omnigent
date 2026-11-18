import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { GlobalHubNavDot } from "./GlobalHubNavDot";
import {
  type BoardSession,
  type BoardWorkerSession,
  useBoardWorkerSessions,
} from "@/hooks/useGlobalHubTasks";
import type { SystemNotice } from "@/hooks/useGlobalHubNotice";
import {
  isReadStateHydrated,
  markConversationSeen,
  resetReadStateForTests,
  seedReadState,
} from "@/hooks/useUnseenConversations";
import { authenticatedFetch } from "@/lib/identity";
import type * as IdentityModule from "@/lib/identity";
import { boardSession, jsonResponse, systemNotice } from "@/test/globalHubFixtures";

vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof IdentityModule>()),
  authenticatedFetch: vi.fn(),
  getCurrentUserId: () => "alice@example.com",
}));

/** Marks the page once the worker sessions have loaded, so a missing dot is not a pending fetch. */
function SessionsLoaded() {
  return useBoardWorkerSessions().isSuccess ? <span data-testid="sessions-loaded" /> : null;
}

function workerSession(id: string, fields: Partial<BoardSession>): BoardWorkerSession {
  return { session_id: id, session: boardSession(fields) };
}

function serve(sessions: BoardWorkerSession[], notice: SystemNotice | null = null): void {
  vi.mocked(authenticatedFetch).mockImplementation(async (input) =>
    String(input).endsWith("/worker-sessions")
      ? jsonResponse({ data: sessions })
      : jsonResponse({ notice }),
  );
}

function renderDot(path = "/globalhub"): QueryClient {
  const client = new QueryClient();
  const page = (
    <>
      <GlobalHubNavDot />
      <SessionsLoaded />
    </>
  );
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/globalhub" element={page} />
          <Route path="/c/:conversationId" element={page} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return client;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  resetReadStateForTests();
});

describe("GlobalHubNavDot", () => {
  it("shows for a worker's finished turn that the user has not seen, and hides once read", async () => {
    seedReadState([{ id: "s1", updated_at: 100 }]);
    serve([workerSession("s1", { status: "idle", updated_at: 200 })]);

    renderDot();

    expect(await screen.findByTestId("globalhub-worker-dot")).toBeInTheDocument();
    act(() => markConversationSeen("s1"));
    expect(screen.queryByTestId("globalhub-worker-dot")).toBeNull();
  });

  it.each([
    { case: "a running turn", status: "running" as const, updatedAt: 200, path: "/globalhub" },
    { case: "a seen turn", status: "idle" as const, updatedAt: 100, path: "/globalhub" },
    {
      case: "the session open on its page",
      status: "idle" as const,
      updatedAt: 200,
      path: "/c/s1",
    },
  ])("stays hidden for $case", async ({ status, updatedAt, path }) => {
    seedReadState([{ id: "s1", updated_at: 100 }]);
    serve([workerSession("s1", { status, updated_at: updatedAt })]);

    renderDot(path);

    expect(await screen.findByTestId("sessions-loaded")).toBeInTheDocument();
    expect(screen.queryByTestId("globalhub-worker-dot")).toBeNull();
  });

  it("reads a session that the session list did not have as seen at load, then shows its next turn", async () => {
    seedReadState([]);
    serve([workerSession("s2", { status: "idle", updated_at: 300 })]);
    const client = renderDot();
    expect(await screen.findByTestId("sessions-loaded")).toBeInTheDocument();
    expect(screen.queryByTestId("globalhub-worker-dot")).toBeNull();

    serve([workerSession("s2", { status: "idle", updated_at: 400 })]);
    await act(() => client.refetchQueries());

    expect(await screen.findByTestId("globalhub-worker-dot")).toBeInTheDocument();
  });

  it("gives no read state before the session list has given its own", async () => {
    serve([workerSession("s3", { status: "idle", updated_at: 300 })]);

    renderDot();

    expect(await screen.findByTestId("sessions-loaded")).toBeInTheDocument();
    expect(isReadStateHydrated()).toBe(false);
  });

  it("shows the notice dot while no worker has news", async () => {
    seedReadState([]);
    serve([], systemNotice("n20"));

    renderDot();

    expect(await screen.findByTestId("globalhub-notice-dot")).toBeInTheDocument();
    expect(screen.queryByTestId("globalhub-worker-dot")).toBeNull();
  });
});
