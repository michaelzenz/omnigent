import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConnectionSettingsBody } from "./ConnectionSettingsSection";
import type { SshConnection } from "@/lib/sshConnectionPreferences";

const mocks = vi.hoisted(() => ({
  connections: [] as SshConnection[],
  packageIndexUrl: null as string | null,
  npmRegistryUrl: null as string | null,
  fetchSshConnections: vi.fn(),
  saveSshConnections: vi.fn(),
  retrySshConnection: vi.fn(),
  fetchSshConnectionLogs: vi.fn(),
}));

vi.mock("@/lib/sshApi", () => ({
  fetchSshConnections: () => mocks.fetchSshConnections(),
  saveSshConnections: (
    connections: SshConnection[],
    packageIndexUrl: string | null,
    npmRegistryUrl: string | null,
  ) => mocks.saveSshConnections(connections, packageIndexUrl, npmRegistryUrl),
  retrySshConnection: (...args: unknown[]) => mocks.retrySshConnection(...args),
  fetchSshConnectionLogs: (...args: unknown[]) => mocks.fetchSshConnectionLogs(...args),
}));

function connection(overrides: Partial<SshConnection> = {}): SshConnection {
  return {
    id: "saved-1",
    label: "Saved",
    alias: "arca.ssh",
    createdAt: "2026-01-01T00:00:00.000Z",
    hostId: "host-1",
    lifecycle: "connected",
    phase: "ready",
    lastError: null,
    attempt: 0,
    nextRetryAt: null,
    updatedAt: "2026-01-01T00:00:00.000Z",
    status: "online",
    warning: null,
    ...overrides,
  };
}

beforeEach(() => {
  mocks.connections = [];
  mocks.packageIndexUrl = null;
  mocks.npmRegistryUrl = null;
  mocks.fetchSshConnections.mockImplementation(async () => ({
    connections: mocks.connections,
    packageIndexUrl: mocks.packageIndexUrl,
    npmRegistryUrl: mocks.npmRegistryUrl,
  }));
  mocks.saveSshConnections.mockImplementation(
    async (
      next: SshConnection[],
      packageIndexUrl: string | null,
      npmRegistryUrl: string | null,
    ) => {
      mocks.connections = next;
      mocks.packageIndexUrl = packageIndexUrl;
      mocks.npmRegistryUrl = npmRegistryUrl;
      return { connections: next, packageIndexUrl, npmRegistryUrl };
    },
  );
  mocks.retrySshConnection.mockResolvedValue(undefined);
  mocks.fetchSshConnectionLogs.mockResolvedValue([
    {
      timestamp: 1_786_000_000,
      time: "2026-01-01T00:00:00.000Z",
      phase: "ready",
      level: "info",
      message: "Host is online and ready",
    },
  ]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ConnectionSettingsBody", () => {
  it("shows empty state when no connections are saved", async () => {
    render(<ConnectionSettingsBody />);
    expect(await screen.findByTestId("ssh-connections-empty")).toBeTruthy();
  });

  it("adds a connection and lets backend reconciliation start", async () => {
    render(<ConnectionSettingsBody />);
    await screen.findByTestId("ssh-connections-empty");
    fireEvent.change(screen.getByTestId("ssh-connection-label"), { target: { value: "Arca" } });
    fireEvent.change(screen.getByTestId("ssh-connection-alias"), { target: { value: "arca.ssh" } });
    fireEvent.click(screen.getByTestId("ssh-connection-add"));

    await waitFor(() => expect(mocks.saveSshConnections).toHaveBeenCalled());
    expect(screen.getByTestId("ssh-connections-list")).toBeTruthy();
    expect(screen.getByText("Arca")).toBeTruthy();
    expect(mocks.connections).toHaveLength(1);
  });

  it("restores durable lifecycle without probing on mount", async () => {
    mocks.connections = [
      connection({
        phase: "backoff",
        status: "offline",
        lastError: "SSH connection timed out",
        attempt: 3,
        nextRetryAt: "2026-01-01T00:01:00.000Z",
      }),
    ];
    render(<ConnectionSettingsBody />);
    expect(await screen.findByText("Waiting to retry")).toBeTruthy();
    expect(screen.getByText("SSH connection timed out")).toBeTruthy();
    expect(screen.getByText("3 attempts")).toBeTruthy();
    expect(screen.getByTestId("ssh-connection-retry-saved-1")).toBeTruthy();
  });

  it("queues an immediate retry from the refresh button", async () => {
    mocks.connections = [
      connection({
        phase: "backoff",
        status: "offline",
        lastError: "Remote host stopped",
        attempt: 2,
      }),
    ];
    render(<ConnectionSettingsBody />);
    fireEvent.click(await screen.findByTestId("ssh-connection-retry-saved-1"));

    await waitFor(() => {
      expect(mocks.retrySshConnection).toHaveBeenCalledWith("saved-1");
    });
  });

  it("expands to show installation logs", async () => {
    mocks.connections = [
      connection({ phase: "installing", status: "offline", hostId: null }),
    ];
    render(<ConnectionSettingsBody />);
    fireEvent.click(await screen.findByTestId("ssh-connection-expand-saved-1"));

    await waitFor(() => {
      expect(mocks.fetchSshConnectionLogs).toHaveBeenCalledWith("saved-1");
    });
    expect(await screen.findByTestId("ssh-connection-logs-saved-1")).toBeTruthy();
    expect(screen.getByText("Host is online and ready")).toBeTruthy();
  });

  it("hides the expand button when the host is online", async () => {
    mocks.connections = [connection()];
    render(<ConnectionSettingsBody />);
    await screen.findByTestId("ssh-connection-row-saved-1");
    expect(screen.queryByTestId("ssh-connection-expand-saved-1")).toBeNull();
  });

  it("shows a flaky warning when the backend reports rapid disconnects", async () => {
    mocks.connections = [
      connection({
        phase: "backoff",
        status: "offline",
        warning: "Connection is flaky — possibly bad network or another server is competing for the socket.",
      }),
    ];
    render(<ConnectionSettingsBody />);
    expect(await screen.findByTestId("ssh-connection-warning-saved-1")).toBeTruthy();
    expect(screen.getByText(/Connection is flaky/)).toBeTruthy();
  });

  it("saves a custom package index URL", async () => {
    render(<ConnectionSettingsBody />);
    await screen.findByTestId("ssh-package-index-form");
    fireEvent.change(screen.getByTestId("ssh-package-index-url"), {
      target: { value: "https://pypi.example.com/simple" },
    });
    fireEvent.click(screen.getByTestId("ssh-package-index-save"));

    await waitFor(() => {
      expect(mocks.saveSshConnections).toHaveBeenCalledWith(
        [],
        "https://pypi.example.com/simple",
        null,
      );
    });
  });

  it("saves a custom npm registry URL", async () => {
    render(<ConnectionSettingsBody />);
    await screen.findByTestId("ssh-package-index-form");
    fireEvent.change(screen.getByTestId("ssh-npm-registry-url"), {
      target: { value: "https://npm.example.com" },
    });
    fireEvent.click(screen.getByTestId("ssh-package-index-save"));

    await waitFor(() => {
      expect(mocks.saveSshConnections).toHaveBeenCalledWith(
        [],
        null,
        "https://npm.example.com",
      );
    });
  });
});
