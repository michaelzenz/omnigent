import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2Icon } from "lucide-react";
import { buildBubbles, createBubbleCache, type Bubble } from "@/lib/renderItems";
import { getCurrentAuthorId } from "@/lib/identity";
import {
  isCostRoutingSession,
  parseCostRoutingVerdict,
} from "@/components/CostRoutingControl";
import { useServerInfo } from "@/lib/CapabilitiesContext";
import { type Agent, useAgents, useSessionAgent } from "@/hooks/useAgents";
import { useRefreshSessionStateOnRunnerOnline } from "@/hooks/useSessionOnlineRefresh";
import { useSessionRunnerOnline } from "@/hooks/RunnerHealthProvider";
import {
  livenessRowFromSession,
  useSessionLiveness,
} from "@/hooks/useSessionLiveness";
import { useSession } from "@/hooks/useSession";
import {
  buildPendingBubbles,
  computeIsWorking,
  computeShowsWorking,
  dispatchInitialPrompt,
  effortLevelsForConv,
  MainAgentSurface,
  mergePendingBubbles,
  modelPickerKindForConv,
  readOnlyReasonForSessionLabels,
  reorderCommittedRequestElicitations,
  shouldQueueSend,
  shouldSendInitialPrompt,
  shouldShowCodexGoalControl,
  shouldShowCodexPlanModeControl,
  shouldShowEffortPicker,
  subAgentComposerLabel,
} from "@/pages/ChatPage";
import {
  consumePendingInitialPrompt,
  useChatStore,
  type PendingInitialPrompt,
} from "@/store/chatStore";
import {
  TerminalFirstContextProvider,
  terminalFirstContextForEmbeddedSession,
} from "./TerminalFirstContext";
import { NewChatComposer } from "./NewChatDialog";

const PUPPY_GARDEN_SESSION_KEY = "omnigent:puppy-garden-session-id";

function readStoredSessionId(): string | null {
  if (typeof localStorage === "undefined") return null;
  return localStorage.getItem(PUPPY_GARDEN_SESSION_KEY);
}

function clearStoredSessionId() {
  if (typeof localStorage !== "undefined") {
    localStorage.removeItem(PUPPY_GARDEN_SESSION_KEY);
  }
}

interface PuppyGardenSessionViewProps {
  sessionId: string;
  /** Prompt to auto-send once the session stream is ready, or null if loading from history. */
  pendingPrompt: PendingInitialPrompt | null;
  onReset: () => void;
}

/**
 * Full session view for the PuppyGarden sidebar. Reuses MainAgentSurface so
 * cursor-native elicitation cards, send queueing, and composer props match
 * the main chat page.
 */
function PuppyGardenSessionView({ sessionId, pendingPrompt, onReset }: PuppyGardenSessionViewProps) {
  const { data: agents, isLoading: agentsLoading, error: agentsError, refetch: refetchAgents } =
    useAgents();
  const { data: boundAgentBySession } = useSessionAgent(sessionId);
  const { session: activeSession, isLoading: sessionLoading } = useSession(sessionId);
  const runnerOnline = useSessionRunnerOnline(sessionId);
  useRefreshSessionStateOnRunnerOnline(sessionId, runnerOnline);

  const blocks = useChatStore((s) => s.blocks);
  const pendingUserMessages = useChatStore((s) => s.pendingUserMessages);
  const activeResponse = useChatStore((s) => s.activeResponse);
  const interruptedResponseIds = useChatStore((s) => s.interruptedResponseIds);
  const loadingConversation = useChatStore((s) => s.loadingConversation);
  const conversationLoadError = useChatStore((s) => s.conversationLoadError);
  const boundAgentId = useChatStore((s) => s.boundAgentId);
  const boundAgentName = useChatStore((s) => s.boundAgentName);
  const status = useChatStore((s) => s.status);
  const sessionStatus = useChatStore((s) => s.sessionStatus);
  const backgroundTaskCount = useChatStore((s) => s.backgroundTaskCount);
  const hasMoreHistory = useChatStore((s) => s.hasMoreHistory);
  const loadingMoreHistory = useChatStore((s) => s.loadingMoreHistory);
  const codexModelOptions = useChatStore((s) => s.codexModelOptions);
  const selectedModel = useChatStore((s) => s.selectedModel);
  const llmModel = useChatStore((s) => s.llmModel);
  const sandboxStatus = useChatStore((s) => s.sandboxStatus);

  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const bubbleCacheRef = useRef(createBubbleCache());
  const initialPromptSentRef = useRef<string | null>(null);

  useEffect(() => {
    void useChatStore.getState().switchTo(sessionId);
  }, [sessionId]);

  useEffect(() => {
    if (conversationLoadError) onReset();
  }, [conversationLoadError, onReset]);

  const agentId = selectedAgentId ?? boundAgentId ?? agents?.[0]?.id ?? null;

  useEffect(() => {
    if (boundAgentId === null) return;
    setSelectedAgentId(boundAgentId);
    if (agents && !agents.some((a) => a.id === boundAgentId)) {
      void refetchAgents();
    }
  }, [boundAgentId, agents, refetchAgents]);

  useEffect(() => {
    if (
      !shouldSendInitialPrompt({
        initialPrompt: pendingPrompt?.text ?? null,
        promptConversationId: sessionId,
        sentForConversationId: initialPromptSentRef.current,
        conversationId: sessionId,
        loadingConversation,
        agentId,
      })
    ) {
      return;
    }
    if (!pendingPrompt || !agentId) return;
    initialPromptSentRef.current = sessionId;
    const { send, sendSlashCommand } = useChatStore.getState();
    dispatchInitialPrompt(pendingPrompt, agentId, send, sendSlashCommand);
  }, [pendingPrompt, sessionId, loadingConversation, agentId]);

  const hasPendingElicitation = useMemo(
    () => blocks.some((b) => b.type === "elicitation" && b.status === "pending"),
    [blocks],
  );

  const bubbles = useMemo<Bubble[]>(() => {
    const committed = reorderCommittedRequestElicitations(
      buildBubbles(blocks, activeResponse, bubbleCacheRef.current, interruptedResponseIds),
    );
    if (pendingUserMessages.length === 0) return committed;
    return mergePendingBubbles(
      committed,
      buildPendingBubbles(pendingUserMessages, getCurrentAuthorId()),
    );
  }, [blocks, activeResponse, interruptedResponseIds, pendingUserMessages]);

  const isWorking = !hasPendingElicitation && computeIsWorking(sessionStatus);
  const showsWorking = computeShowsWorking(sessionStatus, {
    hasPendingElicitation,
    runnerOnline,
    backgroundTaskCount,
  });

  const livenessRow = livenessRowFromSession(activeSession);
  const liveness = useSessionLiveness(sessionId, livenessRow, {
    turnActive: status === "streaming",
  });

  const sandboxLaunching = sandboxStatus !== null && sandboxStatus.stage !== "failed";
  const isUnreachable =
    !sandboxLaunching && (liveness.kind === "host_offline" || liveness.kind === "local_stranded");

  const onSend = useCallback(
    (text: string, files?: File[]) => {
      if (!agentId) return;
      if (isUnreachable) return;
      const chat = useChatStore.getState();
      if (shouldQueueSend(chat.conversationId, chat.status, chat.sessionStatus, chat.queuedMessages)) {
        chat.enqueueMessage(text, files);
        return;
      }
      void chat.send(text, agentId, files);
    },
    [agentId, isUnreachable],
  );

  const onSendSlashCommand = useCallback(
    (name: string, args: string) => {
      if (!agentId || isUnreachable) return;
      void useChatStore.getState().sendSlashCommand(name, args, agentId);
    },
    [agentId, isUnreachable],
  );

  const onStop = useCallback(() => {
    useChatStore.getState().stop();
  }, []);

  const activeSessionLabels = activeSession?.labels;
  const capabilitySource = { labels: activeSessionLabels ?? {} };
  const modelPickerKind = modelPickerKindForConv(capabilitySource);
  const effortLevels = effortLevelsForConv(
    capabilitySource,
    codexModelOptions,
    selectedModel ?? llmModel,
  );
  const showEffort = shouldShowEffortPicker(capabilitySource) && effortLevels.length > 0;
  const permissionLevel = activeSession?.permissionLevel ?? (sessionLoading ? null : 1);
  const readOnlyReason = readOnlyReasonForSessionLabels(activeSession, null);
  const subAgentLabel = subAgentComposerLabel(activeSession);

  const serverInfo = useServerInfo();
  const costRoutingVerdict = useMemo(
    () => parseCostRoutingVerdict(activeSessionLabels),
    [activeSessionLabels],
  );
  const costRoutingEligible =
    serverInfo !== "loading" &&
    serverInfo.smart_routing_enabled &&
    isCostRoutingSession(activeSession);

  const visibleAgents = boundAgentId
    ? boundAgentBySession
      ? [boundAgentBySession]
      : boundAgentName
        ? [{ id: boundAgentId, name: boundAgentName } as Agent]
        : agents?.filter((a) => a.id === boundAgentId)
    : agents;

  const terminalFirstContextValue = useMemo(
    () => terminalFirstContextForEmbeddedSession(activeSessionLabels),
    [activeSessionLabels],
  );

  if (loadingConversation) {
    return (
      <div className="flex flex-1 items-center justify-center gap-2 text-muted-foreground text-sm">
        <Loader2Icon className="size-4 animate-spin" />
        Loading…
      </div>
    );
  }

  return (
    <TerminalFirstContextProvider value={terminalFirstContextValue}>
      <MainAgentSurface
        conversationId={sessionId}
        bubbles={bubbles}
        status={status}
        isWorking={isWorking}
        showsWorking={showsWorking}
        runnerOnline={runnerOnline}
        liveness={liveness}
        agentsError={agentsError}
        disabled={!agentId || agentsError !== null}
        onSend={onSend}
        onSendSlashCommand={onSendSlashCommand}
        onStop={onStop}
        onShowReconnectHelp={() => {}}
        agents={visibleAgents}
        agentsLoading={agentsLoading}
        selectedAgentId={agentId}
        onSelectAgent={setSelectedAgentId}
        hasMoreHistory={hasMoreHistory}
        loadingMoreHistory={loadingMoreHistory}
        permissionLevel={permissionLevel}
        readOnlyReason={readOnlyReason}
        effortLevels={effortLevels}
        showEffort={showEffort}
        showModels={modelPickerKind !== null}
        modelPickerKind={modelPickerKind}
        codexModelOptions={codexModelOptions}
        showCodexPlanMode={shouldShowCodexPlanModeControl(capabilitySource)}
        showCodexGoal={shouldShowCodexGoalControl(capabilitySource)}
        costRoutingVerdict={costRoutingVerdict}
        costRoutingEligible={costRoutingEligible}
        subAgentLabel={subAgentLabel}
      />
    </TerminalFirstContextProvider>
  );
}

/**
 * Right-hand chat rail on the PuppyGarden board. Shows the landing composer
 * until a session is created; then renders the full session (transcript +
 * composer). If the session is later deleted, the rail resets to the landing.
 */
export function PuppyGardenChatSidebar() {
  const [session, setSession] = useState<{
    id: string;
    pendingPrompt: PendingInitialPrompt | null;
  } | null>(() => {
    const id = readStoredSessionId();
    return id ? { id, pendingPrompt: null } : null;
  });

  const handleSessionCreated = useCallback((id: string) => {
    const pendingPrompt = consumePendingInitialPrompt(id);
    localStorage.setItem(PUPPY_GARDEN_SESSION_KEY, id);
    setSession({ id, pendingPrompt });
  }, []);

  const handleReset = useCallback(() => {
    clearStoredSessionId();
    setSession(null);
  }, []);

  return (
    <aside
      className="relative z-10 flex h-full min-h-0 min-w-[300px] flex-col border-border border-l bg-background"
      data-testid="puppy-garden-chat-sidebar"
    >
      {session ? (
        <PuppyGardenSessionView
          sessionId={session.id}
          pendingPrompt={session.pendingPrompt}
          onReset={handleReset}
        />
      ) : (
        <div
          className="flex h-full min-h-0 flex-col p-2"
          data-testid="puppy-garden-chat-composer"
        >
          <NewChatComposer
            navigateOnCreate={false}
            onSessionCreated={handleSessionCreated}
            autoFocus={false}
            compactFooter
          />
        </div>
      )}
    </aside>
  );
}
