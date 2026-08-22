import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SkillsTab } from "./SkillsTab";

const { refreshSkills, refetchSkills, saveSkillFiles, createSkill } = vi.hoisted(() => ({
  refreshSkills: vi.fn(),
  refetchSkills: vi.fn(),
  saveSkillFiles: vi.fn(),
  createSkill: vi.fn(),
}));

vi.mock("@/hooks/useSkills", () => ({
  useSyncedSkills: () => ({
    data: [
      {
        name: "demo",
        description: "Demo skill",
        synced: false,
        syncStatus: "not_synced",
        variants: [
          {
            contentSha256: "aaa",
            activeCount: 1,
            occurrences: [
              {
                name: "demo",
                description: "Demo skill",
                hostId: "host-a",
                hostName: "Laptop",
                harness: "claude",
                relHomePath: ".claude/skills/demo",
                contentSha256: "aaa",
                online: true,
              },
            ],
          },
          {
            contentSha256: "bbb",
            activeCount: 1,
            occurrences: [
              {
                name: "demo",
                description: "Demo skill",
                hostId: "host-b",
                hostName: "Remote",
                harness: "codex",
                relHomePath: ".codex/skills/demo",
                contentSha256: "bbb",
                online: true,
              },
              {
                name: "demo",
                description: "Demo skill",
                hostId: "host-a",
                hostName: "Laptop",
                harness: "omnigent",
                relHomePath: ".omnigent/skills/demo",
                contentSha256: "bbb",
                online: true,
              },
            ],
          },
        ],
        hosts: [
          {
            hostId: "host-a",
            hostName: "Laptop",
            online: true,
            reported: true,
            harnesses: [
              {
                harness: "claude",
                installed: true,
                enabled: true,
                state: "present",
                occurrence: {
                  name: "demo",
                  description: "Demo skill",
                  hostId: "host-a",
                  hostName: "Laptop",
                  harness: "claude",
                  relHomePath: ".claude/skills/demo",
                  contentSha256: "aaa",
                  online: true,
                },
              },
              {
                harness: "codex",
                installed: true,
                enabled: true,
                state: "missing",
                occurrence: null,
              },
              {
                harness: "cursor",
                installed: false,
                enabled: true,
                state: "unavailable",
                occurrence: null,
              },
              {
                harness: "omnigent",
                installed: true,
                enabled: true,
                state: "present",
                occurrence: {
                  name: "demo",
                  description: "Demo skill",
                  hostId: "host-a",
                  hostName: "Laptop",
                  harness: "omnigent",
                  relHomePath: ".omnigent/skills/demo",
                  contentSha256: "bbb",
                  online: true,
                },
              },
            ],
          },
          {
            hostId: "host-b",
            hostName: "Remote",
            online: false,
            reported: false,
            harnesses: [
              {
                harness: "claude",
                installed: true,
                enabled: true,
                state: "offline",
                occurrence: null,
              },
              {
                harness: "codex",
                installed: true,
                enabled: true,
                state: "present",
                occurrence: {
                  name: "demo",
                  description: "Demo skill",
                  hostId: "host-b",
                  hostName: "Remote",
                  harness: "codex",
                  relHomePath: ".codex/skills/demo",
                  contentSha256: "bbb",
                  online: true,
                },
              },
              {
                harness: "cursor",
                installed: false,
                enabled: true,
                state: "unavailable",
                occurrence: null,
              },
            ],
          },
        ],
      },
    ],
    isLoading: false,
    refetch: refetchSkills,
  }),
  useRefreshSkills: () => ({
    mutateAsync: refreshSkills,
    isPending: false,
  }),
  useSkillTree: (_name: string | null, hostId: string | null) => ({
    data:
      hostId === "host-a"
        ? [
            { path: "skill.md", content: "---\nname: demo\n---\n", binary: false },
            { path: "references/a.md", content: "reference", binary: false },
            { path: "assets/image.bin", content: "Binary file (sha256: abc)", binary: true },
          ]
        : hostId === "host-b"
          ? [
              { path: "SKILL.md", content: "---\nname: demo\nchanged: true\n---\n", binary: false },
              { path: "scripts/run.py", content: "print('demo')", binary: false },
            ]
          : undefined,
    isLoading: false,
  }),
  useSkillRoots: () => ({
    data: [
      {
        hostId: "host-a",
        hostName: "Laptop",
        online: true,
        error: null,
        syncHarnesses: { claude: true, codex: true, cursor: false },
        installedHarnesses: { claude: true, codex: true, cursor: false },
        roots: [
          { harness: "claude", relHomePath: ".claude/skills" },
          {
            harness: "claude",
            relHomePath: ".claude/plugins/cache/example/skills",
          },
          { harness: "codex", relHomePath: ".codex/skills" },
          { harness: "cursor", relHomePath: ".cursor/skills" },
        ],
      },
    ],
    isLoading: false,
  }),
  useUpdateSkillHarnessSetting: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
  useSaveSkillVariantFiles: () => ({ mutateAsync: saveSkillFiles, isPending: false }),
  useSyncSkills: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteSkillEverywhere: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateSkill: () => ({ mutateAsync: createSkill, isPending: false }),
}));

describe("SkillsTab", () => {
  beforeEach(() => {
    refreshSkills.mockReset();
    refetchSkills.mockReset();
    saveSkillFiles.mockReset();
    createSkill.mockReset();
  });

  it("shows global sync state and the selected skill editor", async () => {
    render(<SkillsTab />);
    expect(screen.getByText("Not synced")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText("demo")).toHaveLength(2));
    expect(document.querySelectorAll("textarea")).toHaveLength(2);
    expect(screen.getByText("Binary file (sha256: abc)")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show demo variants" }));
    expect(screen.getByText("Claude · Variant 1")).toBeInTheDocument();
    expect(screen.getByText("Codex · Variant Missing")).toBeInTheDocument();
    expect(screen.getByText("Omnigent · Variant 2")).toBeInTheDocument();
    expect(screen.getAllByText("Cursor · Not installed")).toHaveLength(2);
    expect(screen.getByText("Codex · Variant 2")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Claude · Variant 1" }));
    expect(screen.getByText("~/.claude/skills/demo")).toBeInTheDocument();
    expect(screen.queryByText("Edit occurrence")).toBeNull();
    expect(screen.getByRole("button", { name: "Sync to all" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Skill search settings" }));
    expect(screen.getByText("Skill search locations")).toBeInTheDocument();
    expect(screen.getByText("~/.claude/skills")).toBeInTheDocument();
    expect(screen.getByText("~/.claude/plugins/cache/example/skills")).toBeInTheDocument();
    expect(
      screen.getByText("cursor can read claude skills, syncing cursor is unnecessary"),
    ).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Include Claude in skill sync" })).toBeChecked();
    expect(screen.queryByRole("switch", { name: "Include Cursor in skill sync" })).toBeNull();
  });

  it("asks connected hosts to rescan before refetching skills", async () => {
    refreshSkills.mockResolvedValue(undefined);
    refetchSkills.mockResolvedValue(undefined);
    render(<SkillsTab />);

    fireEvent.click(screen.getByRole("button", { name: "Refresh skills from hosts" }));

    await waitFor(() => expect(refreshSkills).toHaveBeenCalledOnce());
    expect(refetchSkills).toHaveBeenCalledOnce();
  });

  it("autosaves edited skill files", async () => {
    vi.useFakeTimers();
    saveSkillFiles.mockResolvedValue({ contentSha256: "updated-hash" });
    render(<SkillsTab />);
    const editor = document.querySelector("textarea");
    expect(editor).not.toBeNull();
    editor?.focus();

    fireEvent.change(editor as HTMLTextAreaElement, {
      target: { value: "---\nname: demo\n---\nupdated\n" },
    });
    expect(screen.getByText("Unsaved")).toBeInTheDocument();
    await vi.advanceTimersByTimeAsync(700);

    expect(saveSkillFiles).toHaveBeenCalledWith({
      name: "demo",
      contentSha256: "aaa",
      files: { "skill.md": "---\nname: demo\n---\nupdated\n" },
    });
    expect(document.activeElement).toBe(editor);
    vi.useRealTimers();
  });

  it("creates a new skill on every detected harness from the dialog", async () => {
    createSkill.mockResolvedValue(undefined);
    render(<SkillsTab />);

    fireEvent.click(screen.getByRole("button", { name: "Create skill" }));
    expect(
      screen.getByText(/Saves the new skill to every detected harness/),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("create-skill-name"), {
      target: { value: "my-new-skill" },
    });
    fireEvent.change(screen.getByTestId("create-skill-description"), {
      target: { value: "A brand new skill" },
    });
    fireEvent.change(screen.getByTestId("create-skill-content"), {
      target: { value: "Do the thing." },
    });
    fireEvent.click(screen.getByTestId("create-skill-submit"));

    await waitFor(() => expect(createSkill).toHaveBeenCalledOnce());
    expect(createSkill).toHaveBeenCalledWith({
      name: "my-new-skill",
      files: {
        "SKILL.md":
          '---\nname: "my-new-skill"\ndescription: "A brand new skill"\n---\n\nDo the thing.\n',
      },
    });
  });

  it("escapes YAML-sensitive skill descriptions", async () => {
    createSkill.mockResolvedValue(undefined);
    render(<SkillsTab />);

    fireEvent.click(screen.getByRole("button", { name: "Create skill" }));
    fireEvent.change(screen.getByTestId("create-skill-name"), {
      target: { value: "deploy-safely" },
    });
    fireEvent.change(screen.getByTestId("create-skill-description"), {
      target: { value: "Deploy: production # carefully\nwithout injection" },
    });
    fireEvent.click(screen.getByTestId("create-skill-submit"));

    await waitFor(() => expect(createSkill).toHaveBeenCalledOnce());
    expect(createSkill.mock.calls[0]?.[0].files["SKILL.md"]).toContain(
      'description: "Deploy: production # carefully\\nwithout injection"',
    );
  });
});
