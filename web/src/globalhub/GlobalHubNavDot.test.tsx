import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { GlobalHubNavDot } from "./GlobalHubNavDot";
import type { SystemNotice } from "@/hooks/useGlobalHubNotice";
import { authenticatedFetch } from "@/lib/identity";
import type * as IdentityModule from "@/lib/identity";
import { jsonResponse, systemNotice } from "@/test/globalHubFixtures";

vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof IdentityModule>()),
  authenticatedFetch: vi.fn(),
  getCurrentUserId: () => "alice@example.com",
}));

function serve(updates: number[], notice: SystemNotice | null = null): void {
  vi.mocked(authenticatedFetch).mockImplementation(async (input) =>
    String(input).includes("/v1/agent-tasks/board")
      ? jsonResponse({ object: "list", data: [], priority_update_counts: updates })
      : jsonResponse({ notice }),
  );
}

function renderDot(): QueryClient {
  const client = new QueryClient();
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/globalhub"]}>
        <GlobalHubNavDot />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return client;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("GlobalHubNavDot", () => {
  it("shows while a task has an update, before the notice dot, and gives way once none has", async () => {
    serve([0, 1, 0, 0], systemNotice("n20"));
    const client = renderDot();

    expect(await screen.findByTestId("globalhub-update-dot")).toBeInTheDocument();
    expect(screen.queryByTestId("globalhub-notice-dot")).toBeNull();
    expect(vi.mocked(authenticatedFetch)).toHaveBeenCalledWith("/v1/agent-tasks/board?limit=1");

    serve([0, 0, 0, 0], systemNotice("n20"));
    await act(() => client.refetchQueries());

    expect(await screen.findByTestId("globalhub-notice-dot")).toBeInTheDocument();
    expect(screen.queryByTestId("globalhub-update-dot")).toBeNull();
  });
});
