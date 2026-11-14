import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { GlobalHubPage } from "./GlobalHubPage";
import { type GlobalHubSettings, useGlobalHubSettings } from "@/hooks/useGlobalHubSettings";

vi.mock("@/hooks/useGlobalHubSettings", () => ({ useGlobalHubSettings: vi.fn() }));
vi.mock("@/globalhub/GlobalHubSettingsDialog", () => ({
  GlobalHubSettingsDialog: ({ open }: { open: boolean }) =>
    open ? <div data-testid="globalhub-settings-dialog" /> : null,
}));

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
  it("asks for a host and a workspace while none is set", () => {
    renderPage({ host_id: null, workspace: null });

    fireEvent.click(screen.getByTestId("globalhub-setup-button"));

    expect(screen.getByTestId("globalhub-settings-dialog")).toBeInTheDocument();
  });

  it("drops the prompt once a host is set, and the gear opens the settings", () => {
    renderPage({ host_id: "host_laptop", workspace: "/Users/alice/hub" });
    const promptShown = screen.queryByTestId("globalhub-setup-prompt") !== null;

    fireEvent.click(screen.getByTestId("globalhub-settings-button"));

    expect(promptShown).toBe(false);
    expect(screen.getByTestId("globalhub-settings-dialog")).toBeInTheDocument();
  });
});
