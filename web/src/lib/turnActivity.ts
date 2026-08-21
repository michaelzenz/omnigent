import type { RenderItem, ToolState } from "./renderItems";

export type TurnActivityCategory = "tool" | "skill" | "mcp";

export interface TurnActivityCall {
  id: string;
  category: TurnActivityCategory;
  name: string;
  serverName?: string;
  operation?: string;
  status: ToolState | "completed";
  agentName: string | null;
  arguments: Record<string, unknown>;
  output: string | null;
  duration?: number;
}

export interface TurnActivityGroup {
  key: string;
  category: TurnActivityCategory;
  name: string;
  serverName?: string;
  operation?: string;
  calls: TurnActivityCall[];
}

export interface TurnActivity {
  groups: TurnActivityGroup[];
  totalCalls: number;
}

function stringField(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function recordField(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function outputField(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return null;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function mcpFunctionName(name: string): { serverName: string; operation: string } | null {
  const match = /^mcp__(.+?)__(.+)$/.exec(name);
  return match ? { serverName: match[1], operation: match[2] } : null;
}

function nativeMcpServer(data: Record<string, unknown>): string | undefined {
  return (
    stringField(data.server_label) ??
    stringField(data.server_name) ??
    stringField(data.serverLabel) ??
    stringField(data.server)
  ) ?? undefined;
}

function callId(item: RenderItem, index: number): string {
  if (item.kind === "tool") return item.execution.callId || item.itemId || `tool-${index}`;
  return item.itemId || `${item.kind}-${index}`;
}

function callsFromItem(item: RenderItem, index: number): TurnActivityCall[] {
  if (item.kind === "slash_command" && item.slashKind === "skill") {
    return [
      {
        id: callId(item, index),
        category: "skill",
        name: item.name,
        status: "completed",
        agentName: null,
        arguments: item.arguments ? { arguments: item.arguments } : {},
        output: item.output,
      },
    ];
  }

  if (item.kind === "native_tool" && item.toolType === "mcp_call") {
    const serverName = nativeMcpServer(item.data);
    const operation = stringField(item.data.name) ?? item.label.replace(/^mcp:\s*/i, "");
    return [
      {
        id: callId(item, index),
        category: "mcp",
        name: serverName ? `${serverName}.${operation}` : operation || "MCP call",
        serverName,
        operation: operation || undefined,
        status: "completed",
        agentName: item.data.agent ? String(item.data.agent) : null,
        arguments: recordField(item.data.arguments ?? item.data.args),
        output: outputField(item.data.output ?? item.data.result),
      },
    ];
  }

  if (item.kind === "native_tool") {
    return [
      {
        id: callId(item, index),
        category: "tool",
        name: item.label || item.toolType,
        status: "completed",
        agentName: item.data.agent ? String(item.data.agent) : null,
        arguments: recordField(item.data.arguments ?? item.data.args),
        output: outputField(item.data.output ?? item.data.result),
      },
    ];
  }

  if (item.kind !== "tool") return [];

  const { execution } = item;
  const mcp = mcpFunctionName(execution.name);
  if (mcp) {
    return [
      {
        id: callId(item, index),
        category: "mcp",
        name: `${mcp.serverName}.${mcp.operation}`,
        serverName: mcp.serverName,
        operation: mcp.operation,
        status: item.state,
        agentName: execution.agentName || null,
        arguments: execution.arguments,
        output: item.output,
        duration: item.duration,
      },
    ];
  }

  if (execution.name === "load_skill" || execution.name === "read_skill_file") {
    const skillName =
      stringField(execution.arguments.name) ??
      stringField(execution.arguments.skill_name) ??
      "Unknown skill";
    return [
      {
        id: callId(item, index),
        category: "skill",
        name: skillName,
        operation: execution.name === "load_skill" ? "Loaded" : "Read resource",
        status: item.state,
        agentName: execution.agentName || null,
        arguments: execution.arguments,
        output: item.output,
        duration: item.duration,
      },
    ];
  }

  return [
    {
      id: callId(item, index),
      category: "tool",
      name: execution.name,
      status: item.state,
      agentName: execution.agentName || null,
      arguments: execution.arguments,
      output: item.output,
      duration: item.duration,
    },
  ];
}

export function collectTurnActivity(items: RenderItem[]): TurnActivity {
  const calls = items.flatMap(callsFromItem);
  const byKey = new Map<string, TurnActivityGroup>();

  for (const call of calls) {
    const key =
      call.category === "mcp"
        ? `mcp:${call.serverName ?? "unknown"}:${call.operation ?? call.name}`
        : `${call.category}:${call.name}`;
    const existing = byKey.get(key);
    if (existing) {
      existing.calls.push(call);
      continue;
    }
    byKey.set(key, {
      key,
      category: call.category,
      name: call.name,
      serverName: call.serverName,
      operation: call.operation,
      calls: [call],
    });
  }

  return { groups: [...byKey.values()], totalCalls: calls.length };
}
