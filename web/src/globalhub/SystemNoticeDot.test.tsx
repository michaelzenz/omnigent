import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SystemNoticeDot } from "./SystemNoticeDot";
import {
  markSystemNoticeSeen,
  type SystemNotice,
  useSystemNotice,
} from "@/hooks/useGlobalHubNotice";
import { authenticatedFetch } from "@/lib/identity";
import type * as IdentityModule from "@/lib/identity";
import { jsonResponse, systemNotice } from "@/test/globalHubFixtures";

vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof IdentityModule>()),
  authenticatedFetch: vi.fn(),
}));

/** Marks the page once the notice has loaded, so a missing dot is not a pending fetch. */
function NoticeLoaded() {
  return useSystemNotice().isSuccess ? <span data-testid="notice-loaded" /> : null;
}

function renderDot(notice: SystemNotice | null): void {
  vi.mocked(authenticatedFetch).mockImplementation(async () => jsonResponse({ notice }));
  const client = new QueryClient();
  render(
    <QueryClientProvider client={client}>
      <SystemNoticeDot />
      <NoticeLoaded />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SystemNoticeDot", () => {
  it("shows for a notice the board has not shown yet, and hides once it has", async () => {
    renderDot(systemNotice("n10"));

    expect(await screen.findByTestId("globalhub-notice-dot")).toBeInTheDocument();
    act(() => markSystemNoticeSeen("n10"));
    expect(screen.queryByTestId("globalhub-notice-dot")).toBeNull();
  });

  it.each([
    { case: "no notice", notice: null },
    { case: "a seen notice", notice: systemNotice("n11") },
  ])("stays hidden for $case", async ({ notice }) => {
    markSystemNoticeSeen("n11");

    renderDot(notice);

    expect(await screen.findByTestId("notice-loaded")).toBeInTheDocument();
    expect(screen.queryByTestId("globalhub-notice-dot")).toBeNull();
  });
});
