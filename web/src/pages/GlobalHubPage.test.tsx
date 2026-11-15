import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { GlobalHubPage } from "./GlobalHubPage";
import { type GlobalHubSettings, useGlobalHubSettings } from "@/hooks/useGlobalHubSettings";

vi.mock("@/hooks/useGlobalHubSettings", () => ({ useGlobalHubSettings: vi.fn() }));
vi.mock("@/globalhub/GlobalHubSettingsDialog", () => ({
  GlobalHubSettingsDialog: ({ open }: { open: boolean }) =>
    open ? <div data-testid="globalhub-settings-dialog" /> : null,
}));
vi.mock("@/globalhub/TaskBoard", () => ({
  TaskBoard: ({ query }: { query?: string }) => (
    <div data-testid="globalhub-task-board" data-query={query ?? ""} />
  ),
}));
vi.mock("@/globalhub/SystemNoticeCard", () => ({
  SystemNoticeCard: () => <div data-testid="globalhub-system-notice" />,
}));
vi.mock("@/globalhub/FyiStream", () => ({
  FyiStream: () => <div data-testid="globalhub-fyi-stream" />,
}));
vi.mock("@/globalhub/BoardConfigDialog", () => ({ BoardConfigButton: () => null }));
vi.mock("@/globalhub/GlobalHubChatPanel", () => ({ GlobalHubChatPanel: () => null }));

function renderPage(data: GlobalHubSettings) {
  vi.mocked(useGlobalHubSettings).mockReturnValue({
    isPending: false,
    isError: false,
    data,
  } as unknown as ReturnType<typeof useGlobalHubSettings>);
  render(<GlobalHubPage />);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("GlobalHubPage", () => {
  it("asks for a host and a workspace while none is set, above the board", () => {
    renderPage({ host_id: null, workspace: null, report_new_sessions: true });
    const boardShown = screen.queryByTestId("globalhub-task-board") !== null;

    fireEvent.click(screen.getByTestId("globalhub-setup-button"));

    expect(boardShown).toBe(true);
    expect(screen.getByTestId("globalhub-settings-dialog")).toBeInTheDocument();
  });

  it("shows only the board once a host is set, and the gear opens the settings", () => {
    renderPage({
      host_id: "host_laptop",
      workspace: "/Users/alice/hub",
      report_new_sessions: true,
    });
    const promptShown = screen.queryByTestId("globalhub-setup-prompt") !== null;

    fireEvent.click(screen.getByTestId("globalhub-settings-button"));

    expect(promptShown).toBe(false);
    expect(screen.getByTestId("globalhub-task-board")).toBeInTheDocument();
    expect(screen.getByTestId("globalhub-settings-dialog")).toBeInTheDocument();
  });

  it("searches the board after a pause in typing, and Escape clears the search", async () => {
    renderPage({ host_id: "host_laptop", workspace: "/srv", report_new_sessions: true });
    const search = screen.getByRole("textbox", { name: "Search tasks" });
    const board = screen.getByTestId("globalhub-task-board");

    fireEvent.change(search, { target: { value: "  retries fix " } });
    const queryWhileTyping = board.dataset.query;
    await waitFor(() => expect(board.dataset.query).toBe("retries fix"));
    // A search shows the board's matches without the notice and the FYI stream.
    const shownWhileSearching = ["globalhub-system-notice", "globalhub-fyi-stream"].filter(
      (testId) => screen.queryByTestId(testId) !== null,
    );
    fireEvent.keyDown(search, { key: "Escape" });

    expect(shownWhileSearching).toEqual([]);
    expect(queryWhileTyping).toBe("");
    expect(search).toHaveValue("");
    await waitFor(() => expect(board.dataset.query).toBe(""));
    expect(screen.getByTestId("globalhub-system-notice")).toBeInTheDocument();
    expect(screen.getByTestId("globalhub-fyi-stream")).toBeInTheDocument();
  });
});
