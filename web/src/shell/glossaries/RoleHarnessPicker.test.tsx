import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { AvailableAgent } from "@/hooks/useAvailableAgents";
import { RoleHarnessPicker } from "./RoleHarnessPicker";

vi.mock("@/shell/NewChatDialog", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/shell/NewChatDialog")>();
  return {
    ...actual,
    AgentHarnessPicker: ({
      agentLabel,
      triggerTestId,
    }: {
      agentLabel: string;
      triggerTestId?: string;
    }) => (
      <button type="button" data-testid={triggerTestId}>
        {agentLabel}
      </button>
    ),
  };
});

const host = {
  host_id: "host-1",
  name: "Local",
  owner: "user",
  status: "online" as const,
  configured_harnesses: {
    "codex-native": true,
    "claude-native": true,
  },
};

const agents: AvailableAgent[] = [
  {
    id: "codex",
    name: "codex-native-ui",
    display_name: "Codex",
    description: null,
    harness: "codex-native",
    skills: [],
  },
  {
    id: "claude",
    name: "claude-native-ui",
    display_name: "Claude Code",
    description: null,
    harness: "claude-native",
    skills: [],
  },
];

describe("RoleHarnessPicker", () => {
  it("prompts for host before showing the harness picker", () => {
    render(
      <RoleHarnessPicker
        host={null}
        agents={agents}
        harness="codex-native"
        model="composer-2.5"
        onChange={() => {}}
      />,
    );
    expect(screen.getByText("Select a host to choose a harness.")).toBeTruthy();
  });

  it("renders the shared new-session harness picker when a host is selected", () => {
    render(
      <RoleHarnessPicker
        host={host}
        agents={agents}
        harness="codex-native"
        model="composer-2.5"
        testId="glossary-role-harness"
        onChange={() => {}}
      />,
    );
    expect(screen.getByTestId("glossary-role-harness")).toHaveTextContent("Codex");
  });
});
