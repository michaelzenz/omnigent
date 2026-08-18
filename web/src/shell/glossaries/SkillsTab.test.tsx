import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SkillsTab } from "./SkillsTab";

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
    refetch: vi.fn(),
  }),
  useSkillTree: (_name: string | null, hostId: string | null) => ({
    data:
      hostId === "host-a"
        ? [
            { path: "SKILL.md", content: "---\nname: demo\n---\n", binary: false },
            { path: "references/a.md", content: "reference", binary: false },
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
  useSaveSkillVariantContent: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useSyncSkills: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteSkillEverywhere: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

describe("SkillsTab", () => {
  it("shows global sync state and the selected skill editor", async () => {
    render(<SkillsTab />);
    expect(screen.getByText("Not synced")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText("demo")).toHaveLength(2));
    expect(document.querySelector("textarea")).toBeInTheDocument();
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
});
