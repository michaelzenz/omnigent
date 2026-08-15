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
import { configuredHarnessesForHost, SDK_HARNESS } from "./roleProfileOptions";

const CLAUDE_NATIVE_DEFAULT_PERMISSION_MODE = "default";
const CODEX_NATIVE_DEFAULT_APPROVAL_MODE = "default";
const CURSOR_NATIVE_DEFAULT_EXEC_MODE = "default";

const SDK_HARNESS_DISPLAY_NAME = "Omnigent";

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

// A synthetic AvailableAgent row for the in-process SDK harness, which is not
// a native CLI and so has no catalog agent that passes isNativeCodingAgent.
// Gives the picker a row to select/label so it stops falling back to the
// first native agent (Claude Code) when the stored harness is the SDK one.
function sdkHarnessAgent(): AvailableAgent {
  return {
    id: SDK_HARNESS,
    name: SDK_HARNESS,
    display_name: SDK_HARNESS_DISPLAY_NAME,
    description: null,
    harness: SDK_HARNESS,
    skills: [],
  };
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
    const native = sortAgentsForDisplay(
      agents.filter(
        (agent) => isNativeCodingAgent(agent) && agent.harness && allowed.has(agent.harness),
      ),
    );
    // The SDK harness is a first-class option but not a native CLI; ensure it
    // has a row even when no catalog agent carrying it survives the native
    // filter (it usually does — task-broker etc. — but be resilient).
    if (allowed.has(SDK_HARNESS) && !native.some((a) => a.harness === SDK_HARNESS)) {
      native.unshift(sdkHarnessAgent());
    }
    return native;
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
      // The SDK harness is not a native CLI and has no permissionMode
      // capability, but it does take a model — preserve the current pick
      // (or the stored model) instead of clearing it like the native CLIs.
      const nextModel =
        nextHarness === SDK_HARNESS
          ? (pickedModel || model || "")
          : modelForHarness(agent, pickedModel);
      onChange({
        harness: nextHarness,
        model: nextModel,
      });
    },
    [harness, onChange, pickedModel, model],
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
