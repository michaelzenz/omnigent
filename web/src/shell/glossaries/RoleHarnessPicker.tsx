import { useCallback, useEffect, useMemo, useState } from "react";
import { sortAgentsForDisplay } from "@/lib/agentGrouping";
import { CLAUDE_NATIVE_MODELS } from "@/lib/claudeNativeModels";
import {
  isNativeCodingAgent,
  nativeAgentHasCapability,
  nativeCodingAgentForAvailableAgent,
} from "@/lib/nativeCodingAgents";
import type { AvailableAgent } from "@/hooks/useAvailableAgents";
import type { Host } from "@/hooks/useHosts";
import { AgentHarnessPicker } from "@/shell/NewChatDialog";
import { configuredHarnessesForHost } from "./roleProfileOptions";

const CLAUDE_NATIVE_DEFAULT_PERMISSION_MODE = "default";
const CODEX_NATIVE_DEFAULT_APPROVAL_MODE = "default";
const CURSOR_NATIVE_DEFAULT_EXEC_MODE = "default";

export interface RoleHarnessPickerProps {
  host: Host | null;
  agents: readonly AvailableAgent[];
  harness: string;
  model: string;
  disabled?: boolean;
  testId?: string;
  onChange: (patch: { harness: string; model: string }) => void;
}

function modelForHarness(agent: AvailableAgent | undefined, pickedModel: string): string {
  if (agent && nativeAgentHasCapability(agent, "permissionMode")) {
    return pickedModel;
  }
  return "";
}

export function RoleHarnessPicker({
  host,
  agents,
  harness,
  model,
  disabled = false,
  testId,
  onChange,
}: RoleHarnessPickerProps) {
  const [permissionMode, setPermissionMode] = useState(CLAUDE_NATIVE_DEFAULT_PERMISSION_MODE);
  const [approvalMode, setApprovalMode] = useState(CODEX_NATIVE_DEFAULT_APPROVAL_MODE);
  const [cursorExecMode, setCursorExecMode] = useState(CURSOR_NATIVE_DEFAULT_EXEC_MODE);
  const [bypassSandbox, setBypassSandbox] = useState(false);
  const [pickedModel, setPickedModel] = useState(model);
  const [pickedEffort, setPickedEffort] = useState("");

  const harnessEntries = useMemo(() => {
    const allowed = new Set(
      configuredHarnessesForHost(host, agents, harness).map((option) => option.harness),
    );
    return sortAgentsForDisplay(
      agents.filter(
        (agent) => isNativeCodingAgent(agent) && agent.harness && allowed.has(agent.harness),
      ),
    );
  }, [host, agents, harness]);

  const effectiveAgentId = useMemo(() => {
    const match = harnessEntries.find((agent) => agent.harness === harness);
    return match?.id ?? harnessEntries[0]?.id ?? null;
  }, [harnessEntries, harness]);

  const selectedAgent = useMemo(
    () => harnessEntries.find((agent) => agent.id === effectiveAgentId) ?? null,
    [harnessEntries, effectiveAgentId],
  );

  const agentLabel = selectedAgent?.display_name ?? (harness || "Select harness");

  useEffect(() => {
    if (!selectedAgent || !nativeAgentHasCapability(selectedAgent, "permissionMode")) return;
    if (model && CLAUDE_NATIVE_MODELS.some((entry) => entry.id === model)) {
      setPickedModel(model);
    }
  }, [selectedAgent, model]);

  const handleSelectAgent = useCallback(
    (agent: AvailableAgent) => {
      const nextHarness =
        nativeCodingAgentForAvailableAgent(agent)?.harness ?? agent.harness ?? harness;
      onChange({
        harness: nextHarness,
        model: modelForHarness(agent, pickedModel),
      });
    },
    [harness, onChange, pickedModel],
  );

  const handlePickedModel = useCallback(
    (nextModel: string) => {
      setPickedModel(nextModel);
      if (selectedAgent && nativeAgentHasCapability(selectedAgent, "permissionMode")) {
        onChange({ harness, model: nextModel });
      }
    },
    [harness, onChange, selectedAgent],
  );

  if (!host?.host_id) {
    return <p className="text-xs text-muted-foreground">Select a host to choose a harness.</p>;
  }

  if (harnessEntries.length === 0) {
    return <p className="text-xs text-muted-foreground">No harnesses configured on this host.</p>;
  }

  return (
    <AgentHarnessPicker
      harnessesOnly
      agentEntries={[]}
      harnessEntries={harnessEntries}
      brainHarnessLabels={{}}
      effectiveAgentId={effectiveAgentId}
      agentLabel={agentLabel}
      hasAgents={!disabled && harnessEntries.length > 0}
      host={host}
      onSelectAgent={handleSelectAgent}
      pendingAgent={null}
      pendingAgentId="__pending__"
      onSelectPending={() => {}}
      onCreateCustomAgent={() => {}}
      permissionMode={permissionMode}
      approvalMode={approvalMode}
      cursorExecMode={cursorExecMode}
      bypassSandbox={bypassSandbox}
      pickedModel={pickedModel}
      pickedEffort={pickedEffort}
      pickedHarness={null}
      setPermissionMode={setPermissionMode}
      setApprovalMode={setApprovalMode}
      setCursorExecMode={setCursorExecMode}
      setBypassSandbox={setBypassSandbox}
      setPickedModel={handlePickedModel}
      setPickedEffort={setPickedEffort}
      setPickedHarness={() => {}}
      triggerTestId={testId}
    />
  );
}
