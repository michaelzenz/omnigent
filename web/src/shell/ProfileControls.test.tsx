import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AvailableAgent } from "@/hooks/useAvailableAgents";
import type * as AgentLabelsModule from "@/lib/agentLabels";
import { ProfileControls } from "./ProfileControls";

const mocks = vi.hoisted(() => ({
  update: vi.fn(),
  archive: vi.fn(),
  create: vi.fn(),
  buildBundle: vi.fn(),
  rows: [] as AvailableAgent[],
}));

vi.mock("@/hooks/useProfiles", () => ({
  useProfiles: () => ({ data: mocks.rows, isLoading: false, isError: false }),
  useUpdateProfileEnabled: () => ({
    mutateAsync: mocks.update,
    variables: undefined,
  }),
  useArchiveProfile: () => ({
    mutateAsync: mocks.archive,
    variables: undefined,
  }),
  useCreateProfile: () => ({
    mutateAsync: mocks.create,
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
    mocks.buildBundle.mockReset();
    mocks.rows = [];
  });

  afterEach(cleanup);

  it("shows Default, Auto Select, and enabled profiles", () => {
    const onSelect = vi.fn();
    render(
      <ProfileControls
        profiles={[profile()]}
        selection="default"
        resolvedAutoProfile={null}
        selectedAgentId="ag_omnigent"
        disabled={false}
        onSelect={onSelect}
      />,
    );

    expect(screen.getByTestId("new-chat-landing-profile-select").textContent).toContain(
      "Profile: Default",
    );
    fireEvent.pointerDown(screen.getByTestId("new-chat-landing-profile-select"), { button: 0 });
    fireEvent.click(screen.getByTestId("new-chat-landing-profile-auto"));
    expect(onSelect).toHaveBeenCalledWith("auto");
  });

  it("displays the resolved profile instead of leaving Auto Select in the label", () => {
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
      "Profile: Researcher",
    );
  });

  it("toggles and deletes custom profiles from the management board", async () => {
    mocks.rows = [profile()];
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
    expect(onSelect).toHaveBeenCalledWith("default");

    fireEvent.click(screen.getByTestId("manage-profile-delete-ag_profile"));
    expect(screen.getByTestId("manage-profile-delete-confirm")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(mocks.archive).toHaveBeenCalledWith("ag_profile"));
  });

  it("creates a durable profile from the management board and selects it", async () => {
    const created = profile({ id: "ag_new", name: "new-profile", display_name: "New Profile" });
    mocks.buildBundle.mockResolvedValue(new File(["bundle"], "agent.tar.gz"));
    mocks.create.mockResolvedValue(created);
    const onSelect = vi.fn();
    render(
      <ProfileControls
        profiles={[]}
        selection="default"
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
});
