import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { GlobalHubPage } from "./GlobalHubPage";
import { type GlobalHubSettings, useGlobalHubSettings } from "@/hooks/useGlobalHubSettings";
import type * as TasksModule from "@/hooks/useGlobalHubTasks";
import type * as IdentityModule from "@/lib/identity";

vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof IdentityModule>()),
  getCurrentUserId: () => "alice@example.com",
}));
vi.mock("@/hooks/useGlobalHubTasks", async (importOriginal) => ({
  ...(await importOriginal<typeof TasksModule>()),
  useBoardPriorityCounts: () => ({ tasks: [2, 5, 1, 0], updates: [1, 0, 0, 0] }),
}));
vi.mock("@/hooks/useGlobalHubSettings", () => ({ useGlobalHubSettings: vi.fn() }));
vi.mock("@/globalhub/GlobalHubSettingsDialog", () => ({
  GlobalHubSettingsDialog: ({ open }: { open: boolean }) =>
    open ? <div data-testid="globalhub-settings-dialog" /> : null,
}));
vi.mock("@/globalhub/TaskBoard", () => ({
  TaskBoard: ({ query, priority }: { query?: string; priority?: number | null }) => (
    <div
      data-testid="globalhub-task-board"
      data-query={query ?? ""}
      data-priority={priority ?? "all"}
    />
  ),
}));
vi.mock("@/globalhub/SystemNoticeCard", () => ({
  SystemNoticeCard: () => <div data-testid="globalhub-system-notice" />,
}));
vi.mock("@/globalhub/FyiStream", () => ({
  FyiStream: () => <div data-testid="globalhub-fyi-stream" />,
}));
vi.mock("@/globalhub/WatcherProblemsCard", () => ({
  WatcherProblemsCard: () => <div data-testid="globalhub-watcher-problems-card" />,
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
  localStorage.clear();
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
    const shownWhileSearching = [
      "globalhub-system-notice",
      "globalhub-watcher-problems-card",
      "globalhub-fyi-stream",
    ].filter((testId) => screen.queryByTestId(testId) !== null);
    fireEvent.keyDown(search, { key: "Escape" });

    expect(shownWhileSearching).toEqual([]);
    expect(queryWhileTyping).toBe("");
    expect(search).toHaveValue("");
    await waitFor(() => expect(board.dataset.query).toBe(""));
    expect(screen.getByTestId("globalhub-system-notice")).toBeInTheDocument();
    expect(screen.getByTestId("globalhub-watcher-problems-card")).toBeInTheDocument();
    expect(screen.getByTestId("globalhub-fyi-stream")).toBeInTheDocument();
  });

  it("shows how many live tasks have each priority, and in all, with a dot where one has news", () => {
    renderPage({ host_id: "host_laptop", workspace: "/srv", report_new_sessions: true });

    const filter = screen.getByTestId("globalhub-priority-filter");
    const chips = Array.from(filter.querySelectorAll("button"));
    expect(chips.map((chip) => chip.getAttribute("aria-label"))).toEqual([
      "P0, 2 tasks, 1 with an update",
      "P1, 5 tasks",
      "P2, 1 task",
      "P3, 0 tasks",
      "All, 8 tasks, 1 with an update",
    ]);
    expect(chips.map((chip) => chip.textContent)).toEqual(["P02", "P15", "P21", "P30", "All8"]);
    expect(
      chips.map(
        (chip) => chip.querySelector('[data-testid="globalhub-priority-update-dot"]') !== null,
      ),
    ).toEqual([true, false, false, false, true]);
  });

  it("filters the board by one priority, remembers it, and All or a second click clears it", () => {
    const settings = { host_id: "host_laptop", workspace: "/srv", report_new_sessions: true };
    const shown = () => screen.getByTestId("globalhub-task-board").dataset.priority;
    const chip = (name: string) => screen.getByRole("button", { name: new RegExp(`^${name}, `) });
    renderPage(settings);

    fireEvent.click(chip("P1"));
    const picked = [shown(), chip("P1").getAttribute("aria-pressed")];
    cleanup();
    renderPage(settings);
    const remembered = shown();
    fireEvent.click(chip("P1"));
    const clickedAgain = shown();
    fireEvent.click(chip("P0"));
    const switched = shown();
    fireEvent.click(chip("All"));

    expect(picked).toEqual(["1", "true"]);
    expect([remembered, clickedAgain, switched, shown()]).toEqual(["1", "all", "0", "all"]);
    expect(chip("All")).toHaveAttribute("aria-pressed", "true");
  });

  it("starts a search at All, keeps a priority picked during it, and else restores the kept one", async () => {
    const settings = { host_id: "host_laptop", workspace: "/srv", report_new_sessions: true };
    const board = () => screen.getByTestId("globalhub-task-board");
    const chip = (name: string) => screen.getByRole("button", { name: new RegExp(`^${name}, `) });
    const search = () => screen.getByRole("textbox", { name: "Search tasks" });
    const searchFor = async (text: string) => {
      fireEvent.change(search(), { target: { value: text } });
      await waitFor(() => expect(board().dataset.query).toBe(text));
    };
    renderPage(settings);
    fireEvent.click(chip("P1"));

    await searchFor("retries");
    const searched = [board().dataset.priority, chip("All").getAttribute("aria-pressed")];
    fireEvent.keyDown(search(), { key: "Escape" });
    await waitFor(() => expect(board().dataset.query).toBe(""));
    const restored = board().dataset.priority;
    await searchFor("retries");
    fireEvent.click(chip("P2"));
    const pickedInSearch = board().dataset.priority;
    fireEvent.click(screen.getByRole("button", { name: "Clear search" }));
    await waitFor(() => expect(board().dataset.query).toBe(""));
    const kept = board().dataset.priority;
    cleanup();
    renderPage(settings);

    expect(searched).toEqual(["all", "true"]);
    expect([restored, pickedInSearch, kept, board().dataset.priority]).toEqual([
      "1",
      "2",
      "2",
      "2",
    ]);
  });
});
