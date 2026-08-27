import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PromptProfile } from "@/hooks/usePromptProfiles";
import { ProfileControls } from "./ProfileControls";

const mocks = vi.hoisted(() => ({
  update: vi.fn(),
  deleteProfile: vi.fn(),
  create: vi.fn(),
  updateSettings: vi.fn(),
  rows: [] as PromptProfile[],
}));
vi.mock("@/lib/agentLabels", () => ({
  useBrainHarnessLabels: () => ({
    "claude-sdk": "Claude SDK",
    codex: "Codex",
    cursor: "Cursor",
    pi: "Pi",
  }),
  BRAIN_HARNESS_LABELS: { "claude-sdk": "Claude SDK" },
}));
vi.mock("@/hooks/useModelSettings", () => {
  // Stable reference so useEffect depending on data doesn't re-fire every render.
  const data = { systemPrompt: "", promptProfileAutoIncludeLimit: 5 };
  return {
    useOmniHarnessSettings: () => ({ data }),
    useUpdateOmniHarnessSettings: () => ({
      mutateAsync: mocks.updateSettings,
      isPending: false,
    }),
  };
});

vi.mock("@/hooks/usePromptProfiles", () => ({
  usePromptProfiles: () => ({ data: mocks.rows, isLoading: false, isError: false }),
  useUpdatePromptProfile: () => ({
    mutateAsync: mocks.update,
    isPending: false,
    variables: undefined,
  }),
  useDeletePromptProfile: () => ({
    mutateAsync: mocks.deleteProfile,
    isPending: false,
    variables: undefined,
  }),
  useCreatePromptProfile: () => ({
    mutateAsync: mocks.create,
    isPending: false,
  }),
}));

function profile(overrides: Partial<PromptProfile> = {}): PromptProfile {
  return {
    id: "profile_research",
    name: "Research",
    description: "Investigates difficult questions",
    instructions: "Cite sources",
    enabled: true,
    created_at: 1,
    updated_at: 1,
    ...overrides,
  };
}

describe("ProfileControls", () => {
  beforeEach(() => {
    mocks.update.mockReset();
    mocks.deleteProfile.mockReset();
    mocks.create.mockReset();
    mocks.updateSettings.mockReset();
    mocks.rows = [];
  });

  it("updates the Auto Include maximum from profile management", async () => {
    mocks.updateSettings.mockResolvedValue({
      systemPrompt: "",
      promptProfileAutoIncludeLimit: 7,
    });
    render(
      <ProfileControls
        profiles={[]}
        selection="auto_include"
        selectedProfileId={null}
        disabled={false}
        onSelect={() => {}}
      />,
    );

    fireEvent.click(screen.getByTestId("new-chat-landing-profile-gear"));
    fireEvent.change(screen.getByTestId("manage-profiles-auto-include-limit"), {
      target: { value: "7" },
    });
    fireEvent.click(screen.getByTestId("manage-profiles-auto-include-save"));

    await waitFor(() =>
      expect(mocks.updateSettings).toHaveBeenCalledWith({
        promptProfileAutoIncludeLimit: 7,
      }),
    );
  });

  afterEach(cleanup);

  it("shows Auto Select and the supplied enabled profiles", () => {
    const onSelect = vi.fn();
    render(
      <ProfileControls
        profiles={[profile()]}
        selection="auto"
        selectedProfileId={null}
        disabled={false}
        onSelect={onSelect}
      />,
    );

    fireEvent.pointerDown(screen.getByTestId("new-chat-landing-profile-select"), { button: 0 });
    fireEvent.click(screen.getByTestId("new-chat-landing-profile-profile_research"));
    expect(onSelect).toHaveBeenCalledWith(
      "profile_research",
      expect.objectContaining({ name: "Research" }),
    );
  });

  it("shows disabled profiles in management and toggles enabled", async () => {
    mocks.rows = [profile({ enabled: false })];
    mocks.update.mockResolvedValue(profile());
    render(
      <ProfileControls
        profiles={[]}
        selection="auto"
        selectedProfileId={null}
        disabled={false}
        onSelect={() => {}}
      />,
    );

    fireEvent.click(screen.getByTestId("new-chat-landing-profile-gear"));
    expect(screen.getByTestId("manage-profile-row-profile_research")).toBeTruthy();
    fireEvent.click(screen.getByTestId("manage-profile-enabled-profile_research"));
    await waitFor(() =>
      expect(mocks.update).toHaveBeenCalledWith({ id: "profile_research", enabled: true }),
    );
  });

  it("creates a profile from direct text fields", async () => {
    const created = profile({ id: "profile_new", name: "New profile" });
    mocks.create.mockResolvedValue(created);
    const onSelect = vi.fn();
    render(
      <ProfileControls
        profiles={[]}
        selection="auto"
        selectedProfileId={null}
        disabled={false}
        onSelect={onSelect}
      />,
    );

    fireEvent.click(screen.getByTestId("new-chat-landing-profile-gear"));
    fireEvent.click(screen.getByTestId("manage-profiles-add"));
    // Harness and model fields must not appear for prompt profiles.
    expect(screen.queryByTestId("create-agent-harness")).toBeNull();
    expect(screen.queryByTestId("create-agent-model")).toBeNull();
    fireEvent.change(screen.getByTestId("create-agent-name"), {
      target: { value: "New profile" },
    });
    fireEvent.change(screen.getByTestId("create-agent-instructions"), {
      target: { value: "Be concise" },
    });
    fireEvent.click(screen.getByTestId("create-agent-submit"));

    await waitFor(() =>
      expect(mocks.create).toHaveBeenCalledWith({
        name: "New profile",
        description: null,
        instructions: "Be concise",
        enabled: true,
      }),
    );
    expect(onSelect).toHaveBeenCalledWith("profile_new", created);
  });

  it("edits a profile and saves without requiring harness or model", async () => {
    mocks.rows = [profile()];
    mocks.update.mockResolvedValue(profile({ name: "Research v2" }));
    render(
      <ProfileControls
        profiles={[]}
        selection="auto"
        selectedProfileId={null}
        disabled={false}
        onSelect={() => {}}
      />,
    );

    fireEvent.click(screen.getByTestId("new-chat-landing-profile-gear"));
    fireEvent.click(screen.getByTestId("manage-profile-edit-profile_research"));
    // Harness and model fields must not appear for prompt profiles.
    expect(screen.queryByTestId("create-agent-harness")).toBeNull();
    expect(screen.queryByTestId("create-agent-model")).toBeNull();
    fireEvent.change(screen.getByTestId("create-agent-name"), {
      target: { value: "Research v2" },
    });
    fireEvent.change(screen.getByTestId("create-agent-instructions"), {
      target: { value: "Cite all sources" },
    });
    // Submit must not be disabled — model is not required for profiles.
    expect(
      (screen.getByTestId("create-agent-submit") as HTMLButtonElement).disabled,
    ).toBe(false);
    fireEvent.click(screen.getByTestId("create-agent-submit"));

    await waitFor(() =>
      expect(mocks.update).toHaveBeenCalledWith({
        id: "profile_research",
        name: "Research v2",
        description: "Investigates difficult questions",
        instructions: "Cite all sources",
      }),
    );
  });
});
