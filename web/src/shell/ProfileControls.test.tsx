import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PromptProfile } from "@/hooks/usePromptProfiles";
import { ProfileControls } from "./ProfileControls";

const mocks = vi.hoisted(() => ({
  update: vi.fn(),
  archive: vi.fn(),
  create: vi.fn(),
  rows: [] as PromptProfile[],
}));

vi.mock("@/hooks/usePromptProfiles", () => ({
  usePromptProfiles: () => ({ data: mocks.rows, isLoading: false, isError: false }),
  useUpdatePromptProfile: () => ({
    mutateAsync: mocks.update,
    isPending: false,
    variables: undefined,
  }),
  useArchivePromptProfile: () => ({
    mutateAsync: mocks.archive,
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
    archived: false,
    created_at: 1,
    updated_at: 1,
    ...overrides,
  };
}

describe("ProfileControls", () => {
  beforeEach(() => {
    mocks.update.mockReset();
    mocks.archive.mockReset();
    mocks.create.mockReset();
    mocks.rows = [];
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
});
