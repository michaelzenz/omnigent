import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { GlobalHubSettingsDialog } from "./GlobalHubSettingsDialog";
import type { GlobalHubSettings } from "@/hooks/useGlobalHubSettings";
import { type Host, useHosts } from "@/hooks/useHosts";
import { authenticatedFetch } from "@/lib/identity";
import type * as IdentityModule from "@/lib/identity";
import { localTimezone } from "@/lib/timezones";

vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof IdentityModule>()),
  authenticatedFetch: vi.fn(),
}));
vi.mock("@/hooks/useHosts", () => ({ useHosts: vi.fn() }));
vi.mock("@/hooks/useRecentWorkspaces", () => ({
  useRecentWorkspaces: () => ({ recent: [], addRecent: vi.fn() }),
}));
vi.mock("@/shell/HostLabel", () => ({
  HostLabel: ({ host }: { host: { name: string } }) => <span>{host.name}</span>,
}));
// The path field and the folder browser read the host's filesystem; their own
// suites cover that.
vi.mock("@/shell/WorkspacePathField", () => ({
  WorkspacePathField: ({ value, onChange }: { value: string; onChange: (v: string) => void }) => (
    <input
      data-testid="mock-workspace-input"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
}));
vi.mock("@/shell/WorkspacePickerDialog", () => ({ WorkspacePickerDialog: () => null }));
// Radix Select uses a portal and pointer events that jsdom cannot drive.
vi.mock("@/components/ui/select", () => ({
  Select: ({
    value,
    onValueChange,
    children,
  }: {
    value: string;
    onValueChange: (v: string) => void;
    children: ReactNode;
  }) => (
    <select
      data-testid="mock-host-select"
      value={value}
      onChange={(e) => onValueChange(e.target.value)}
    >
      <option value="">none</option>
      {children}
    </select>
  ),
  SelectTrigger: ({ children }: { children: ReactNode }) => children,
  SelectValue: () => null,
  SelectContent: ({ children }: { children: ReactNode }) => children,
  SelectItem: ({
    value,
    disabled,
    children,
    "data-testid": testId,
  }: {
    value: string;
    disabled?: boolean;
    children: ReactNode;
    "data-testid"?: string;
  }) => (
    <option value={value} disabled={disabled} data-testid={testId}>
      {children}
    </option>
  ),
}));

const fetchMock = vi.mocked(authenticatedFetch);
const HOSTS: Host[] = [
  { host_id: "host_laptop", name: "laptop", owner: "alice", status: "online" },
  { host_id: "host_desk", name: "desk", owner: "alice", status: "offline" },
];
const PLACEMENT = { host_id: "host_laptop", workspace: "/Users/alice/hub" };
const SWEEP = { enabled: true, time: "09:00", timezone: "America/Los_Angeles" };
const SAVED: GlobalHubSettings = {
  ...PLACEMENT,
  report_new_sessions: true,
  custodian_sweep: { ...SWEEP, next_run_at: 1_800_000_000 },
};
const UNSET: GlobalHubSettings = { host_id: null, workspace: null, report_new_sessions: true };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderDialog(settings: GlobalHubSettings) {
  const onOpenChange = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <GlobalHubSettingsDialog open onOpenChange={onOpenChange} settings={settings} />
    </QueryClientProvider>,
  );
  return onOpenChange;
}

function sentRequest(): { method: string | undefined; body: unknown } {
  const [path, init] = fetchMock.mock.calls[0];
  expect(path).toBe("/v1/settings/globalhub");
  return { method: init?.method, body: JSON.parse(String(init?.body)) };
}

beforeEach(() => {
  vi.mocked(useHosts).mockReturnValue({ data: HOSTS } as unknown as ReturnType<typeof useHosts>);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("GlobalHubSettingsDialog", () => {
  it("offers only online hosts and saves once a host and a workspace are chosen", async () => {
    fetchMock.mockResolvedValue(json(SAVED));
    const onOpenChange = renderDialog(UNSET);
    const save = screen.getByTestId("globalhub-settings-save");
    expect(screen.getByTestId("globalhub-host-option-host_desk")).toBeDisabled();
    expect(save).toBeDisabled();

    fireEvent.change(screen.getByTestId("mock-host-select"), {
      target: { value: "host_laptop" },
    });
    fireEvent.change(screen.getByTestId("mock-workspace-input"), {
      target: { value: " /Users/alice/hub " },
    });
    fireEvent.click(save);

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    // A first save turns the daily cleanup on at 09:00 in the browser's timezone.
    expect(sentRequest()).toEqual({
      method: "PUT",
      body: {
        ...PLACEMENT,
        report_new_sessions: true,
        custodian_sweep: { ...SWEEP, timezone: localTimezone() },
      },
    });
  });

  it("clears a saved setting with nulls", async () => {
    fetchMock.mockResolvedValue(json(UNSET));
    const onOpenChange = renderDialog(SAVED);

    fireEvent.click(screen.getByTestId("globalhub-settings-clear"));

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(sentRequest()).toEqual({ method: "PUT", body: { ...UNSET, custodian_sweep: SWEEP } });
  });

  it("saves the switch that stops new sessions reporting to the broker", async () => {
    fetchMock.mockResolvedValue(json({ ...SAVED, report_new_sessions: false }));
    const onOpenChange = renderDialog(SAVED);

    fireEvent.click(screen.getByRole("switch", { name: "Send new sessions to the broker" }));
    fireEvent.click(screen.getByTestId("globalhub-settings-save"));

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(sentRequest()).toEqual({
      method: "PUT",
      body: { ...PLACEMENT, report_new_sessions: false, custodian_sweep: SWEEP },
    });
  });

  it("saves the daily cleanup's time and timezone, and needs a whole time to save", async () => {
    fetchMock.mockResolvedValue(json(SAVED));
    const onOpenChange = renderDialog(SAVED);
    const time = screen.getByLabelText("Cleanup time");

    fireEvent.change(time, { target: { value: "" } });
    expect(screen.getByTestId("globalhub-settings-save")).toBeDisabled();
    fireEvent.change(time, { target: { value: "18:30" } });
    await userEvent.click(screen.getByLabelText("Timezone"));
    fireEvent.change(screen.getByLabelText("Search timezones"), { target: { value: "Berlin" } });
    await userEvent.click(await screen.findByRole("option", { name: "Europe/Berlin" }));
    fireEvent.click(screen.getByTestId("globalhub-settings-save"));

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(sentRequest()).toEqual({
      method: "PUT",
      body: {
        ...PLACEMENT,
        report_new_sessions: true,
        custodian_sweep: { enabled: true, time: "18:30", timezone: "Europe/Berlin" },
      },
    });
  });

  it("stays open and shows the server's reason when a save is refused", async () => {
    fetchMock.mockResolvedValue(json({ detail: "The host is offline" }, 400));
    const onOpenChange = renderDialog(SAVED);

    fireEvent.click(screen.getByTestId("globalhub-settings-save"));

    expect(await screen.findByTestId("globalhub-settings-error")).toHaveTextContent(
      "The host is offline",
    );
    expect(onOpenChange).not.toHaveBeenCalled();
  });
});
