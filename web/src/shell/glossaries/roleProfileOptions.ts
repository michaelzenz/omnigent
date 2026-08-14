import { harnessUnconfiguredOnHost, harnessWarningBadgeText } from "@/shell/NewChatDialog";
import type { Host } from "@/hooks/useHosts";
import type { AvailableAgent } from "@/hooks/useAvailableAgents";
import { isNativeCodingAgent, nativeCodingAgentForAvailableAgent } from "@/lib/nativeCodingAgents";

export interface HarnessOption {
  harness: string;
  displayName: string;
}

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
