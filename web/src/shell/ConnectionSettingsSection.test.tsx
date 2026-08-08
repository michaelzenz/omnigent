import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConnectionSettingsBody } from "./ConnectionSettingsSection";
import type { SshConnection } from "@/lib/sshConnectionPreferences";

const mocks = vi.hoisted(() => ({
  connections: [] as SshConnection[],
  packageIndexUrl: null as string | null,
  fetchSshConnections: vi.fn(),
  saveSshConnections: vi.fn(),
  retrySshConnection: vi.fn(),
  testSshConnection: vi.fn(),
}));

vi.mock("@/lib/sshApi", () => ({
  fetchSshConnections: () => mocks.fetchSshConnections(),
  saveSshConnections: (connections: SshConnection[], packageIndexUrl: string | null) =>
    mocks.saveSshConnections(connections, packageIndexUrl),
  retrySshConnection: (...args: unknown[]) => mocks.retrySshConnection(...args),
  testSshConnection: (...args: unknown[]) => mocks.testSshConnection(...args),
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
    ...overrides,
  };
}

beforeEach(() => {
  mocks.connections = [];
  mocks.packageIndexUrl = null;
  mocks.fetchSshConnections.mockImplementation(async () => ({
    connections: mocks.connections,
    packageIndexUrl: mocks.packageIndexUrl,
  }));
  mocks.saveSshConnections.mockImplementation(
    async (next: SshConnection[], packageIndexUrl: string | null) => {
      mocks.connections = next;
      mocks.packageIndexUrl = packageIndexUrl;
      return { connections: next, packageIndexUrl };
    },
  );
  mocks.retrySshConnection.mockResolvedValue(undefined);
  mocks.testSshConnection.mockResolvedValue({ ok: true, message: "Connected", latencyMs: 12 });
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
    expect(mocks.testSshConnection).not.toHaveBeenCalled();
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
    expect(mocks.testSshConnection).not.toHaveBeenCalled();
  });

  it("queues an immediate retry from backoff", async () => {
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

  it("keeps Test SSH explicit", async () => {
    mocks.connections = [connection()];
    render(<ConnectionSettingsBody />);
    fireEvent.click(await screen.findByTestId("ssh-connection-retest-saved-1"));

    await waitFor(() => {
      expect(mocks.testSshConnection).toHaveBeenCalledWith("arca.ssh");
    });
  });

  it("saves a custom package index URL", async () => {
    render(<ConnectionSettingsBody />);
    await screen.findByTestId("ssh-package-index-form");
    fireEvent.change(screen.getByTestId("ssh-package-index-url"), {
      target: { value: "https://pypi.example.com/simple" },
    });
    fireEvent.click(screen.getByTestId("ssh-package-index-save"));

    await waitFor(() => {
      expect(mocks.saveSshConnections).toHaveBeenCalledWith([], "https://pypi.example.com/simple");
    });
  });
});
