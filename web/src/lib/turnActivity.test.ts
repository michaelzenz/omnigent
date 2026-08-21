import { describe, expect, it } from "vitest";

import type { RenderItem } from "./renderItems";
import { collectTurnActivity } from "./turnActivity";

function tool(
  name: string,
  arguments_: Record<string, unknown>,
  callId: string,
): Extract<RenderItem, { kind: "tool" }> {
  return {
    kind: "tool",
    itemId: callId,
    execution: {
      name,
      arguments: arguments_,
      argsSummary: "",
      callId,
      agentName: "coder",
      executedBy: "server",
      output: "done",
    },
    output: "done",
    state: "output-available",
    startedAt: 1,
    duration: 2,
  };
}

describe("collectTurnActivity", () => {
  it("groups repeated tools while retaining every call", () => {
    const activity = collectTurnActivity([
      tool("read_file", { path: "a.ts" }, "call_1"),
      tool("read_file", { path: "b.ts" }, "call_2"),
    ]);

    expect(activity.totalCalls).toBe(2);
    expect(activity.groups).toHaveLength(1);
    expect(activity.groups[0]).toMatchObject({
      category: "tool",
      name: "read_file",
    });
    expect(activity.groups[0].calls.map((call) => call.id)).toEqual(["call_1", "call_2"]);
  });

  it("classifies prefixed MCP calls by server and operation", () => {
    const activity = collectTurnActivity([
      tool("mcp__slack__search_messages", { query: "incident" }, "mcp_1"),
    ]);

    expect(activity.groups[0]).toMatchObject({
      category: "mcp",
      serverName: "slack",
      operation: "search_messages",
    });
  });

  it("classifies native MCP records and keeps provider details", () => {
    const activity = collectTurnActivity([
      {
        kind: "native_tool",
        itemId: "native_1",
        toolType: "mcp_call",
        label: "mcp: list_resources",
        data: {
          server_label: "filesystem",
          name: "list_resources",
          arguments: { path: "/" },
          result: ["a", "b"],
        },
      },
    ]);

    expect(activity.groups[0]).toMatchObject({
      category: "mcp",
      serverName: "filesystem",
      operation: "list_resources",
    });
    expect(activity.groups[0].calls[0].output).toContain('"a"');
  });

  it("attributes slash invocations and skill tools to Skills", () => {
    const activity = collectTurnActivity([
      {
        kind: "slash_command",
        itemId: "slash_1",
        slashKind: "skill",
        name: "github-workflow",
        arguments: "review",
        output: null,
      },
      tool("load_skill", { name: "canvas" }, "skill_1"),
      tool("read_skill_file", { skill_name: "canvas", path: "SKILL.md" }, "skill_2"),
    ]);

    expect(activity.groups.map((group) => [group.category, group.name])).toEqual([
      ["skill", "github-workflow"],
      ["skill", "canvas"],
    ]);
    expect(activity.groups[1].calls).toHaveLength(2);
  });

  it("ignores prose, reasoning, and surfaced non-skill commands", () => {
    const activity = collectTurnActivity([
      { kind: "text", itemId: "text_1", text: "done", final: true },
      { kind: "reasoning", itemId: "reason_1", text: "thinking", duration: 1 },
      {
        kind: "slash_command",
        itemId: "command_1",
        slashKind: "command",
        name: "model",
        arguments: "opus",
        output: null,
      },
    ]);

    expect(activity).toEqual({ groups: [], totalCalls: 0 });
  });
});
