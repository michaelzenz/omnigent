import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { GlobalHubSettingsDialog } from "./GlobalHubSettingsDialog";
import type { GlobalHubSettings } from "@/hooks/useGlobalHubSettings";
import { type Host, useHosts } from "@/hooks/useHosts";
import { authenticatedFetch } from "@/lib/identity";
import type * as IdentityModule from "@/lib/identity";

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
const SAVED: GlobalHubSettings = { host_id: "host_laptop", workspace: "/Users/alice/hub" };

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
    const onOpenChange = renderDialog({ host_id: null, workspace: null });
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
    expect(sentRequest()).toEqual({ method: "PUT", body: SAVED });
  });

  it("clears a saved setting with nulls", async () => {
    fetchMock.mockResolvedValue(json({ host_id: null, workspace: null }));
    const onOpenChange = renderDialog(SAVED);

    fireEvent.click(screen.getByTestId("globalhub-settings-clear"));

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(sentRequest()).toEqual({ method: "PUT", body: { host_id: null, workspace: null } });
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
