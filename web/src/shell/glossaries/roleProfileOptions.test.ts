import { describe, expect, it } from "vitest";
import { configuredHarnessesForHost } from "./roleProfileOptions";

describe("configuredHarnessesForHost", () => {
  it("filters harnesses using host configured_harnesses", () => {
    const host = {
      host_id: "host-1",
      name: "dev",
      owner: "local",
      status: "online" as const,
      configured_harnesses: { "cursor-native": true, "codex-native": false },
    };
    const agents = [
      {
        id: "a1",
        name: "cursor-native-ui",
        display_name: "Cursor",
        harness: "cursor-native",
        description: null,
        skills: [],
      },
      {
        id: "a2",
        name: "codex-native-ui",
        display_name: "Codex",
        harness: "codex-native",
        description: null,
        skills: [],
      },
    ];
    const harnesses = configuredHarnessesForHost(host, agents);
    // The in-process SDK harness is always available (no host config), so it
    // appears alongside the host-configured native CLIs.
    expect(harnesses.map((entry) => entry.harness)).toEqual(["cursor-native", "openai-agents"]);
  });

  it("returns no harnesses when host is not selected", () => {
    const agents = [
      {
        id: "a1",
        name: "cursor-native-ui",
        display_name: "Cursor",
        harness: "cursor-native",
        description: null,
        skills: [],
      },
    ];
    expect(configuredHarnessesForHost(null, agents)).toEqual([]);
  });
});
