import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { FyiStream } from "./FyiStream";
import type { FyiCluster } from "@/hooks/useGlobalHubFyi";
import { authenticatedFetch } from "@/lib/identity";
import type * as IdentityModule from "@/lib/identity";
import { fyiCluster, fyiEvent, jsonResponse } from "@/test/globalHubFixtures";

vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof IdentityModule>()),
  authenticatedFetch: vi.fn(),
}));

const fetchMock = vi.mocked(authenticatedFetch);

function renderStream(clusters: FyiCluster[]): void {
  fetchMock.mockImplementation(async (input) =>
    String(input) === "/v1/agent-tasks/board/pending"
      ? jsonResponse({ fyi: clusters })
      : jsonResponse({}),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <FyiStream />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("FyiStream", () => {
  it("lists the clusters oldest first, each signal with a link to its session", async () => {
    renderStream([
      fyiCluster("c1", {
        headline: "Docs sessions",
        rationale: "No task tracks the docs work.",
        events: [fyiEvent("e1"), fyiEvent("e2", { title: "Manual note", source_session_id: null })],
      }),
      fyiCluster("c2", { headline: "Spikes", events: [fyiEvent("e3")] }),
    ]);

    const stream = within(await screen.findByTestId("globalhub-fyi"));
    expect(stream.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual([
      "Docs sessions",
      "Spikes",
    ]);
    expect(stream.getByText("(3 signals)")).toBeInTheDocument();
    expect(stream.getByText("No task tracks the docs work.")).toBeInTheDocument();
    const first = within(screen.getByTestId("fyi-cluster-c1"));
    expect(first.getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual([
      "/c/s-e1",
    ]);
    expect(first.getByText("Manual note")).toBeInTheDocument();

    fireEvent.click(first.getByRole("button", { name: "Hide signals" }));

    expect(first.queryByText("Manual note")).toBeNull();
  });

  it("links a watcher's signal to its source, not to the watcher session", async () => {
    renderStream([
      fyiCluster("c1", {
        events: [
          fyiEvent("e1", {
            event_type: "watch.changed",
            title: "alice sent a direct message",
            source_session_id: "s-watcher",
            source_key: "https://chat.example.com/archives/D0123",
          }),
          fyiEvent("e2", {
            event_type: "watch.new",
            title: "A ticket was assigned to you",
            source_session_id: "s-watcher",
            source_key: "ES-1234",
          }),
        ],
      }),
    ]);

    const card = within(await screen.findByTestId("fyi-cluster-c1"));
    expect(card.getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual([
      "https://chat.example.com/archives/D0123",
    ]);
  });

  it("dismisses a cluster", async () => {
    renderStream([fyiCluster("c1", { events: [fyiEvent("e1")] })]);

    fireEvent.click(await screen.findByRole("button", { name: "Dismiss" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/v1/fyi-clusters/c1/resolve", { method: "POST" }),
    );
  });
});
