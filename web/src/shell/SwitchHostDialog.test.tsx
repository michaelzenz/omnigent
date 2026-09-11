import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SwitchHostDialog } from "./SwitchHostDialog";
import { useHosts } from "@/hooks/useHosts";
import { useHostFilesystem } from "@/hooks/useHostFilesystem";
import { useHostRepository } from "@/hooks/useHostWorktrees";
import { useSession } from "@/hooks/useSession";
import { launchRunner, updateSession } from "@/lib/sessionsApi";

// Heavy children have their own suites; stub them so this one stays on the
// dialog's two-call move and its recovery from a half-finished switch.
vi.mock("./WorkspacePathField", () => ({
  WorkspacePathField: ({ value, onChange }: { value: string; onChange: (v: string) => void }) => (
    <input
      data-testid="mock-workspace-input"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
}));
vi.mock("./WorkspacePicker", () => ({
  WorkspacePicker: () => <div data-testid="mock-workspace-picker" />,
  homeFromEntries: () => null,
  isNavigablePath: () => false,
}));
vi.mock("./HostLabel", () => ({
  HostLabel: ({ host }: { host: { name: string } }) => <span>{host.name}</span>,
}));
vi.mock("@/hooks/useHosts", () => ({ useHosts: vi.fn() }));
vi.mock("@/hooks/useHostFilesystem", () => ({ useHostFilesystem: vi.fn() }));
vi.mock("@/hooks/useHostWorktrees", () => ({ useHostRepository: vi.fn() }));
vi.mock("@/hooks/useSession", () => ({ useSession: vi.fn() }));
vi.mock("@/hooks/useRecentWorkspaces", () => ({
  useRecentWorkspaces: () => ({ recent: ["/Users/alice/repo"], addRecent: vi.fn() }),
}));
vi.mock("@/lib/sessionsApi", () => ({ launchRunner: vi.fn(), updateSession: vi.fn() }));
// Radix Select uses a portal + pointer events jsdom can't drive; a native
// <select> keeps the option list assertable.
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
      {children}
    </select>
  ),
  SelectTrigger: ({ children }: { children: ReactNode }) => children,
  SelectValue: () => null,
  SelectContent: ({ children }: { children: ReactNode }) => children,
  SelectItem: ({
    value,
    children,
    "data-testid": testId,
  }: {
    value: string;
    children: ReactNode;
    "data-testid"?: string;
  }) => (
    <option value={value} data-testid={testId}>
      {children}
    </option>
  ),
}));

const useHostsMock = vi.mocked(useHosts);
const useHostFilesystemMock = vi.mocked(useHostFilesystem);
const useHostRepositoryMock = vi.mocked(useHostRepository);
const useSessionMock = vi.mocked(useSession);
const launchRunnerMock = vi.mocked(launchRunner);
const updateSessionMock = vi.mocked(updateSession);

function renderDialog() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SwitchHostDialog open onOpenChange={() => {}} sessionId="conv_1" currentHostId="host_old" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useHostsMock.mockReset();
  useHostFilesystemMock.mockReset();
  useHostRepositoryMock.mockReset();
  useSessionMock.mockReset();
  launchRunnerMock.mockReset();
  updateSessionMock.mockReset();
  useHostsMock.mockReturnValue({
    data: [
      { host_id: "host_old", name: "mac-laptop", owner: "alice", status: "online" },
      { host_id: "host_new", name: "linux-box", owner: "alice", status: "online" },
    ],
  } as unknown as ReturnType<typeof useHosts>);
  useHostFilesystemMock.mockReturnValue({
    data: undefined,
    isPlaceholderData: false,
  } as unknown as ReturnType<typeof useHostFilesystem>);
  // Not a git repo by default — the auto-worktree toggle stays hidden and
  // the launch binds the directory directly, as before.
  useHostRepositoryMock.mockReturnValue({
    data: undefined,
  } as unknown as ReturnType<typeof useHostRepository>);
  useSessionMock.mockReturnValue({
    session: { id: "conv_1", title: "Fix the login retry flake" },
    isLoading: false,
    error: null,
  } as unknown as ReturnType<typeof useSession>);
  updateSessionMock.mockResolvedValue({} as Awaited<ReturnType<typeof updateSession>>);
  launchRunnerMock.mockResolvedValue({ runnerId: "runner_new" });
});

afterEach(() => cleanup());

describe("SwitchHostDialog", () => {
  it("releases the runner and the model override before launching on the new host", async () => {
    renderDialog();

    const button = await screen.findByTestId("switch-host-button");
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button);

    // The model id is resolved against the old host's catalog, so it has to
    // go with the binding — otherwise the next turn asks the new host for a
    // model it may not have. `silent` keeps the reset out of the transcript.
    await waitFor(() => expect(updateSessionMock).toHaveBeenCalledTimes(1));
    expect(updateSessionMock).toHaveBeenCalledWith("conv_1", {
      runnerId: "",
      modelOverride: null,
      silent: true,
    });
    await waitFor(() => expect(launchRunnerMock).toHaveBeenCalledTimes(1));
    expect(launchRunnerMock).toHaveBeenCalledWith("host_new", "conv_1", "/Users/alice/repo");
    // The launch endpoint binds with `WHERE runner_id IS NULL`, so a launch
    // that raced ahead of the release would be rejected outright.
    expect(updateSessionMock.mock.invocationCallOrder[0]).toBeLessThan(
      launchRunnerMock.mock.invocationCallOrder[0],
    );
  });

  it("offers the origin host again when the launch fails after the release", async () => {
    launchRunnerMock.mockRejectedValue(new Error("host is offline"));
    renderDialog();

    // Moving to the host it is already on is a no-op, so the origin starts
    // out of the list.
    expect(screen.queryByTestId("switch-host-option-host_old")).toBeNull();
    expect(screen.getByTestId("switch-host-option-host_new")).toBeInTheDocument();

    const button = await screen.findByTestId("switch-host-button");
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button);

    // Released but not re-bound: the session is on no host at all, so the
    // origin becomes a real target and the copy has to say what happened
    // rather than read as two unrelated outages.
    const error = await screen.findByTestId("switch-host-error");
    expect(error.textContent).toContain("isn't running anywhere");
    expect(error.textContent).toContain("host is offline");
    expect(screen.getByTestId("switch-host-option-host_old")).toBeInTheDocument();
  });

  it("hides the auto-worktree toggle when the directory is not a git folder", () => {
    renderDialog();

    expect(screen.queryByTestId("switch-host-auto-worktree-toggle")).toBeNull();
  });

  it("binds the directory directly when auto worktree is off", async () => {
    useHostRepositoryMock.mockReturnValue({
      data: { isGitRepository: true, worktrees: [], autoWorktreesSupported: true },
    } as unknown as ReturnType<typeof useHostRepository>);
    renderDialog();

    const button = await screen.findByTestId("switch-host-button");
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button);

    await waitFor(() => expect(launchRunnerMock).toHaveBeenCalledTimes(1));
    expect(launchRunnerMock).toHaveBeenCalledWith("host_new", "conv_1", "/Users/alice/repo");
  });

  it("creates a managed worktree off the directory when auto worktree is on", async () => {
    useHostRepositoryMock.mockReturnValue({
      data: { isGitRepository: true, worktrees: [], autoWorktreesSupported: true },
    } as unknown as ReturnType<typeof useHostRepository>);
    renderDialog();

    const toggle = await screen.findByTestId("switch-host-auto-worktree-toggle");
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-pressed")).toBe("true");

    const button = await screen.findByTestId("switch-host-button");
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button);

    await waitFor(() => expect(launchRunnerMock).toHaveBeenCalledTimes(1));
    // The prompt is the session title — the server turns it into a
    // semantic branch name (fail-open to a random slug).
    expect(launchRunnerMock).toHaveBeenCalledWith("host_new", "conv_1", "/Users/alice/repo", {
      autoCreate: true,
      branchNamePrompt: "Fix the login retry flake",
    });
  });

  it("disarms the toggle when the directory stops being a git folder", async () => {
    useHostRepositoryMock.mockReturnValue({
      data: { isGitRepository: true, worktrees: [], autoWorktreesSupported: true },
    } as unknown as ReturnType<typeof useHostRepository>);
    renderDialog();

    const toggle = await screen.findByTestId("switch-host-auto-worktree-toggle");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-pressed")).toBe("true");

    // Editing the field to a non-repo path drops the gate; the armed
    // toggle must not survive it — the next submit would branch a
    // directory that isn't a repo.
    useHostRepositoryMock.mockReturnValue({
      data: { isGitRepository: false, worktrees: [], autoWorktreesSupported: true },
    } as unknown as ReturnType<typeof useHostRepository>);
    fireEvent.change(screen.getByTestId("mock-workspace-input"), {
      target: { value: "/Users/alice/plain" },
    });

    await waitFor(() =>
      expect(screen.queryByTestId("switch-host-auto-worktree-toggle")).toBeNull(),
    );
    const button = await screen.findByTestId("switch-host-button");
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(launchRunnerMock).toHaveBeenCalledTimes(1));
    expect(launchRunnerMock).toHaveBeenCalledWith("host_new", "conv_1", "/Users/alice/plain");
  });
});
