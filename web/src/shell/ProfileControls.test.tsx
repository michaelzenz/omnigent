import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AvailableAgent } from "@/hooks/useAvailableAgents";
import type * as AgentLabelsModule from "@/lib/agentLabels";
import { ProfileControls } from "./ProfileControls";

const mocks = vi.hoisted(() => ({
  update: vi.fn(),
  archive: vi.fn(),
  create: vi.fn(),
  edit: vi.fn(),
  updatePending: false,
  updateVariables: undefined as { id: string } | undefined,
  buildBundle: vi.fn(),
  rows: [] as AvailableAgent[],
}));

vi.mock("@/hooks/useProfiles", () => ({
  useProfiles: () => ({ data: mocks.rows, isLoading: false, isError: false }),
  useUpdateProfileEnabled: () => ({
    mutateAsync: mocks.update,
    isPending: mocks.updatePending,
    variables: mocks.updateVariables,
  }),
  useArchiveProfile: () => ({
    mutateAsync: mocks.archive,
    variables: undefined,
  }),
  useCreateProfile: () => ({
    mutateAsync: mocks.create,
    isPending: false,
  }),
  useEditProfile: () => ({
    mutateAsync: mocks.edit,
    variables: undefined,
    isPending: false,
  }),
}));

vi.mock("@/lib/agentBundle", () => ({
  buildAgentBundle: mocks.buildBundle,
}));

vi.mock("@/lib/agentLabels", async (importOriginal) => ({
  ...(await importOriginal<typeof AgentLabelsModule>()),
  useBrainHarnessLabels: () => ({ "claude-sdk": "Claude SDK" }),
}));

function profile(overrides: Partial<AvailableAgent> = {}): AvailableAgent {
  return {
    id: "ag_profile",
    name: "researcher",
    display_name: "Researcher",
    description: "Investigates difficult questions",
    harness: "claude-sdk",
    skills: [],
    builtin: false,
    enabled: true,
    archived: false,
    is_multi_agent: false,
    subagent_count: 0,
    default_harness: "claude-sdk",
    default_model: "sonnet",
    ...overrides,
  };
}

describe("ProfileControls", () => {
  beforeEach(() => {
    mocks.update.mockReset();
    mocks.archive.mockReset();
    mocks.create.mockReset();
    mocks.edit.mockReset();
    mocks.updatePending = false;
    mocks.updateVariables = undefined;
    mocks.buildBundle.mockReset();
    mocks.rows = [];
  });

  afterEach(cleanup);

  it("shows Auto Select and enabled profiles without a Default option", () => {
    const onSelect = vi.fn();
    render(
      <ProfileControls
        profiles={[profile()]}
        selection="auto"
        resolvedAutoProfile={null}
        selectedAgentId="ag_omnigent"
        disabled={false}
        onSelect={onSelect}
      />,
    );

    expect(screen.getByTestId("new-chat-landing-profile-select").textContent).toContain(
      "Profile: Auto Select",
    );
    fireEvent.pointerDown(screen.getByTestId("new-chat-landing-profile-select"), { button: 0 });
    expect(screen.queryByText("Default")).toBeNull();
    fireEvent.click(screen.getByTestId("new-chat-landing-profile-auto"));
    expect(onSelect).toHaveBeenCalledWith("auto");
  });

  it("keeps the Auto Select label because selection runs per turn", () => {
    render(
      <ProfileControls
        profiles={[profile()]}
        selection="auto"
        resolvedAutoProfile={profile()}
        selectedAgentId="ag_profile"
        disabled={false}
        onSelect={() => {}}
      />,
    );

    expect(screen.getByTestId("new-chat-landing-profile-select").textContent).toContain(
      "Profile: Auto Select",
    );
  });

  it("toggles and deletes custom profiles from the management board", async () => {
    mocks.rows = [profile(), profile({ id: "ag_peer", name: "peer", display_name: "Peer" })];
    mocks.update.mockResolvedValue(profile({ enabled: false }));
    mocks.archive.mockResolvedValue(undefined);
    const onSelect = vi.fn();
    render(
      <ProfileControls
        profiles={[profile()]}
        selection="ag_profile"
        resolvedAutoProfile={null}
        selectedAgentId="ag_profile"
        disabled={false}
        onSelect={onSelect}
      />,
    );

    fireEvent.click(screen.getByTestId("new-chat-landing-profile-gear"));
    expect(screen.getByRole("heading", { name: "Manage profiles" })).toBeTruthy();
    fireEvent.click(screen.getByTestId("manage-profile-enabled-ag_profile"));
    await waitFor(() =>
      expect(mocks.update).toHaveBeenCalledWith({ id: "ag_profile", enabled: false }),
    );
    expect(onSelect).toHaveBeenCalledWith("auto");

    fireEvent.click(screen.getByTestId("manage-profile-delete-ag_profile"));
    expect(screen.getByTestId("manage-profile-delete-confirm")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(mocks.archive).toHaveBeenCalledWith("ag_profile"));
  });

  it("blocks disabling or deleting the last profile", () => {
    mocks.rows = [profile()];
    render(
      <ProfileControls
        profiles={[profile()]}
        selection="ag_profile"
        resolvedAutoProfile={null}
        selectedAgentId="ag_profile"
        disabled={false}
        onSelect={() => {}}
      />,
    );

    fireEvent.click(screen.getByTestId("new-chat-landing-profile-gear"));
    expect(screen.getByTestId("manage-profile-enabled-ag_profile")).toBeDisabled();
    expect(screen.getByTestId("manage-profile-delete-ag_profile")).toBeDisabled();
  });

  it("does not keep showing pending after a completed toggle retains variables", () => {
    mocks.rows = [profile()];
    mocks.updateVariables = { id: "ag_profile" };
    render(
      <ProfileControls
        profiles={[profile()]}
        selection="ag_profile"
        resolvedAutoProfile={null}
        selectedAgentId="ag_profile"
        disabled={false}
        onSelect={() => {}}
      />,
    );

    fireEvent.click(screen.getByTestId("new-chat-landing-profile-gear"));
    expect(screen.queryByTestId("manage-profile-pending-ag_profile")).toBeNull();
  });

  it("creates a durable profile from the management board and selects it", async () => {
    const created = profile({ id: "ag_new", name: "new-profile", display_name: "New Profile" });
    mocks.buildBundle.mockResolvedValue(new File(["bundle"], "agent.tar.gz"));
    mocks.create.mockResolvedValue(created);
    const onSelect = vi.fn();
    render(
      <ProfileControls
        profiles={[]}
        selection="auto"
        resolvedAutoProfile={null}
        selectedAgentId="ag_omnigent"
        disabled={false}
        onSelect={onSelect}
      />,
    );

    fireEvent.click(screen.getByTestId("new-chat-landing-profile-gear"));
    fireEvent.click(screen.getByTestId("manage-profiles-add"));
    fireEvent.change(screen.getByTestId("create-agent-name"), {
      target: { value: "new-profile" },
    });
    fireEvent.click(screen.getByTestId("create-agent-submit"));

    await waitFor(() =>
      expect(mocks.buildBundle).toHaveBeenCalledWith({
        name: "new-profile",
        description: undefined,
        instructions: undefined,
        mcpServers: undefined,
      }),
    );
    expect(mocks.create).toHaveBeenCalledWith(expect.any(File));
    expect(onSelect).toHaveBeenCalledWith("ag_new", created);
  });

  it("edits a custom profile from the pencil action", async () => {
    const existing = profile({ instructions: "Before" });
    const updated = profile({
      name: "edited",
      display_name: "Edited",
      instructions: "After",
    });
    mocks.rows = [existing, profile({ id: "ag_peer", name: "peer", display_name: "Peer" })];
    mocks.edit.mockResolvedValue(updated);
    const onSelect = vi.fn();
    render(
      <ProfileControls
        profiles={[existing]}
        selection="ag_profile"
        resolvedAutoProfile={null}
        selectedAgentId="ag_profile"
        disabled={false}
        onSelect={onSelect}
      />,
    );

    fireEvent.click(screen.getByTestId("new-chat-landing-profile-gear"));
    fireEvent.click(screen.getByTestId("manage-profile-edit-ag_profile"));
    fireEvent.change(screen.getByTestId("create-agent-name"), {
      target: { value: "edited" },
    });
    fireEvent.change(screen.getByTestId("create-agent-instructions"), {
      target: { value: "After" },
    });
    fireEvent.click(screen.getByTestId("create-agent-submit"));

    await waitFor(() =>
      expect(mocks.edit).toHaveBeenCalledWith({
        id: "ag_profile",
        name: "edited",
        description: "Investigates difficult questions",
        instructions: "After",
      }),
    );
    expect(onSelect).toHaveBeenCalledWith("ag_profile", updated);
  });
});
