import { harnessUnconfiguredOnHost, harnessWarningBadgeText } from "@/shell/NewChatDialog";
import type { Host } from "@/hooks/useHosts";
import type { AvailableAgent } from "@/hooks/useAvailableAgents";
import { isNativeCodingAgent, nativeCodingAgentForAvailableAgent } from "@/lib/nativeCodingAgents";

export interface HarnessOption {
  harness: string;
  displayName: string;
}

// The in-process SDK harness is always available — no CLI binary, no host
// config — so it's a first-class option in the role picker regardless of the
// host's configured_harnesses map. Surfaced as "Omnigent" (the PuppyGarden
// SDK executor path).
export const SDK_HARNESS = "openai-agents";
const SDK_HARNESS_DISPLAY_NAME = "Omnigent";

// Models the in-process SDK executor can route to behind the Databricks
// AI Gateway. Kept short on purpose — the gateway serves more, but these are
// the two PuppyGarden roles are seeded with (see DATABRICKS-INSTALL.md).
export interface SdkModelOption {
  id: string;
  displayName: string;
}

export const SDK_MODEL_OPTIONS: readonly SdkModelOption[] = [
  { id: "databricks-glm-5-2", displayName: "GLM 5.2" },
  { id: "databricks-kimi-k3", displayName: "Kimi K3" },
];

/** Harnesses configured on the selected host (same filter as New Chat). */
export function configuredHarnessesForHost(
  host: Host | null | undefined,
  agents: readonly AvailableAgent[],
  currentHarness?: string | null,
): HarnessOption[] {
  if (!host?.host_id) {
    const current = currentHarness?.trim();
    return current ? [{ harness: current, displayName: current }] : [];
  }
  const seen = new Set<string>();
  const options: HarnessOption[] = [];

  // The SDK harness is always available (in-process, no host setup), so list
  // it first regardless of the host's configured_harnesses map.
  options.push({ harness: SDK_HARNESS, displayName: SDK_HARNESS_DISPLAY_NAME });
  seen.add(SDK_HARNESS);

  for (const agent of agents) {
    if (!isNativeCodingAgent(agent) || !agent.harness) continue;
    if (harnessUnconfiguredOnHost(agent.harness, host)) continue;
    if (seen.has(agent.harness)) continue;
    seen.add(agent.harness);
    const spec = nativeCodingAgentForAvailableAgent(agent);
    options.push({
      harness: agent.harness,
      displayName: spec?.displayName ?? agent.harness,
    });
  }

  options.sort((a, b) => {
    const rankA =
      nativeCodingAgentForAvailableAgent(
        agents.find((agent) => agent.harness === a.harness) ?? {
          harness: a.harness,
          name: a.harness,
        },
      )?.sortRank ?? Number.POSITIVE_INFINITY;
    const rankB =
      nativeCodingAgentForAvailableAgent(
        agents.find((agent) => agent.harness === b.harness) ?? {
          harness: b.harness,
          name: b.harness,
        },
      )?.sortRank ?? Number.POSITIVE_INFINITY;
    return rankA - rankB || a.displayName.localeCompare(b.displayName);
  });

  const current = currentHarness?.trim();
  if (current && !seen.has(current)) {
    options.unshift({
      harness: current,
      displayName: current,
    });
  }

  return options;
}

export { harnessWarningBadgeText };
