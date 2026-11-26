import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { WatcherProblemsCard } from "./WatcherProblemsCard";
import type { WatcherProblem } from "@/hooks/useGlobalHubFyi";
import { authenticatedFetch } from "@/lib/identity";
import type * as IdentityModule from "@/lib/identity";
import { jsonResponse, watcherProblem } from "@/test/globalHubFixtures";

vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof IdentityModule>()),
  authenticatedFetch: vi.fn(),
}));

const fetchMock = vi.mocked(authenticatedFetch);

function renderCard(problems: WatcherProblem[]): void {
  fetchMock.mockImplementation(async (input) =>
    String(input) === "/v1/agent-tasks/board/pending"
      ? jsonResponse({ fyi: [], watcher_problems: problems })
      : jsonResponse({ dismissed: problems.length }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <WatcherProblemsCard />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("WatcherProblemsCard", () => {
  it("shows every watcher that does not work in one card", async () => {
    renderCard([
      watcherProblem("w1", {
        watcher_name: "Slack watcher",
        sources: [
          {
            source: "slack.conversation",
            source_key: "https://chat.example.com/archives/D0123",
            reason: "the privacy filter hides the message text",
          },
          { source: "slack.thread", source_key: null, reason: "no access to the MCP server" },
        ],
        note: "Sign in to the Slack MCP server on the host.",
      }),
      watcherProblem("w2", { watcher_name: "Jira watcher", run_error: "auth command failed" }),
    ]);

    const card = within(await screen.findByTestId("globalhub-watcher-problems"));
    expect(screen.getAllByTestId("globalhub-watcher-problems")).toHaveLength(1);
    expect(card.getByRole("heading", { level: 3 })).toHaveTextContent(
      "2 watchers are not working correctly",
    );
    expect(card.getByText(/the privacy filter hides the message text/)).toBeInTheDocument();
    expect(card.getByText(/no access to the MCP server/)).toBeInTheDocument();
    expect(card.getByText("Sign in to the Slack MCP server on the host.")).toBeInTheDocument();
    expect(card.getByText("Last run failed: auth command failed")).toBeInTheDocument();
    expect(card.getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual([
      "/c/s-w1",
      "https://chat.example.com/archives/D0123",
      "/c/s-w2",
    ]);
  });

  it("dismisses the watchers it shows", async () => {
    renderCard([watcherProblem("w1"), watcherProblem("w2")]);

    fireEvent.click(await screen.findByRole("button", { name: "Dismiss" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/v1/agent-tasks/board/watcher-problems/dismiss",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ watcher_ids: ["w1", "w2"] }),
        }),
      ),
    );
  });

  it("is hidden while every watcher works", async () => {
    renderCard([]);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(screen.queryByTestId("globalhub-watcher-problems")).toBeNull();
  });
});
