import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConnectionSettingsBody } from "./ConnectionSettingsSection";
import type { SshConnection } from "@/lib/sshConnectionPreferences";

const mocks = vi.hoisted(() => ({
  connections: [] as SshConnection[],
  fetchSshConnections: vi.fn(),
  saveSshConnections: vi.fn(),
  testSshConnection: vi.fn(),
}));

vi.mock("@/lib/sshApi", () => ({
  fetchSshConnections: () => mocks.fetchSshConnections(),
  saveSshConnections: (next: SshConnection[]) => mocks.saveSshConnections(next),
  testSshConnection: (...args: unknown[]) => mocks.testSshConnection(...args),
}));

beforeEach(() => {
  mocks.connections = [];
  mocks.fetchSshConnections.mockImplementation(async () => mocks.connections);
  mocks.saveSshConnections.mockImplementation(async (next: SshConnection[]) => {
    mocks.connections = next;
    return next;
  });
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

  it("adds a connection and auto-tests it", async () => {
    render(<ConnectionSettingsBody />);
    await screen.findByTestId("ssh-connections-empty");
    fireEvent.change(screen.getByTestId("ssh-connection-label"), { target: { value: "Arca" } });
    fireEvent.change(screen.getByTestId("ssh-connection-alias"), { target: { value: "arca.ssh" } });
    fireEvent.click(screen.getByTestId("ssh-connection-add"));

    await waitFor(() => {
      expect(mocks.testSshConnection).toHaveBeenCalledWith("arca.ssh");
    });
    expect(screen.getByTestId("ssh-connections-list")).toBeTruthy();
    expect(screen.getByText("Arca")).toBeTruthy();
    expect(mocks.connections).toHaveLength(1);
  });

  it("probes saved connections on mount", async () => {
    mocks.connections = [
      {
        id: "saved-1",
        label: "Saved",
        alias: "arca.ssh",
        codexRemote: true,
        createdAt: "2026-01-01T00:00:00.000Z",
      },
    ];
    render(<ConnectionSettingsBody />);
    await waitFor(() => {
      expect(mocks.testSshConnection).toHaveBeenCalledWith("arca.ssh");
    });
  });
});
