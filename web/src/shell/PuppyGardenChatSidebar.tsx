import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2Icon, RotateCcwIcon } from "lucide-react";
import { buildBubbles, createBubbleCache, type Bubble } from "@/lib/renderItems";
import { getCurrentAuthorId } from "@/lib/identity";
import {
  isCostRoutingSession,
  parseCostRoutingVerdict,
} from "@/components/CostRoutingControl";
import { useServerInfo } from "@/lib/CapabilitiesContext";
import { type Agent, useAgents, useSessionAgent } from "@/hooks/useAgents";
import { useResetSecretarySession, useSecretaryProfile, useSecretarySession } from "@/hooks/useAgentTasks";
import { useRefreshSessionStateOnRunnerOnline } from "@/hooks/useSessionOnlineRefresh";
import { useSessionRunnerOnline, useRunnerHealthRegistration } from "@/hooks/RunnerHealthProvider";
import {
  livenessRowFromSession,
  useSessionLiveness,
} from "@/hooks/useSessionLiveness";
import { useSession } from "@/hooks/useSession";
import {
  buildPendingBubbles,
  computeIsWorking,
  computeShowsWorking,
  effortLevelsForConv,
  MainAgentSurface,
  mergePendingBubbles,
  modelPickerKindForConv,
  readOnlyReasonForSessionLabels,
  reorderCommittedRequestElicitations,
  shouldQueueSend,
  shouldShowCodexGoalControl,
  shouldShowCodexPlanModeControl,
  shouldShowEffortPicker,
  subAgentComposerLabel,
} from "@/pages/ChatPage";
import { useChatStore } from "@/store/chatStore";
import {
  TerminalFirstContextProvider,
  terminalFirstContextForEmbeddedSession,
} from "./TerminalFirstContext";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface PuppyGardenSessionViewProps {
  sessionId: string;
  onReload: () => void;
  onReset: () => void;
  resetPending: boolean;
}

/**
 * Full session view for the PuppyGarden sidebar. Reuses MainAgentSurface so
 * cursor-native elicitation cards, send queueing, and composer props match
 * the main chat page.
 */
function PuppyGardenSessionView({
  sessionId,
  onReload: _onReload,
  onReset,
  resetPending,
}: PuppyGardenSessionViewProps) {
  const { data: agents, isLoading: agentsLoading, error: agentsError, refetch: refetchAgents } =
    useAgents();
  const { data: boundAgentBySession } = useSessionAgent(sessionId);
  const { session: activeSession, isLoading: sessionLoading } = useSession(sessionId);
  const runnerHealthSessions = useMemo(() => [{ id: sessionId }], [sessionId]);
  // Secretary sessions are hidden from the sidebar, so register for the
  // app-wide /health poll — otherwise runner liveness stays unknown and
  // ConnectionIndicator loops on "Connecting…".
  useRunnerHealthRegistration(runnerHealthSessions);
  const runnerOnline = useSessionRunnerOnline(sessionId);
  useRefreshSessionStateOnRunnerOnline(sessionId, runnerOnline);

  const blocks = useChatStore((s) => s.blocks);
  const pendingUserMessages = useChatStore((s) => s.pendingUserMessages);
  const activeResponse = useChatStore((s) => s.activeResponse);
  const interruptedResponseIds = useChatStore((s) => s.interruptedResponseIds);
  const loadingConversation = useChatStore((s) => s.loadingConversation);
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

  useEffect(() => {
    void useChatStore.getState().switchTo(sessionId);
  }, [sessionId]);

  const agentId = selectedAgentId ?? boundAgentId ?? agents?.[0]?.id ?? null;

  useEffect(() => {
    if (boundAgentId === null) return;
    setSelectedAgentId(boundAgentId);
    if (agents && !agents.some((a) => a.id === boundAgentId)) {
      void refetchAgents();
    }
  }, [boundAgentId, agents, refetchAgents]);

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
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex shrink-0 items-center justify-between border-border border-b px-2 py-1.5">
          <span className="font-medium text-sm">Task secretary</span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 gap-1.5 px-2 text-muted-foreground"
            onClick={onReset}
            disabled={resetPending}
            data-testid="puppy-garden-secretary-reset"
          >
            {resetPending ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <RotateCcwIcon className="size-3.5" />
            )}
            Reset
          </Button>
        </div>
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
      </div>
    </TerminalFirstContextProvider>
  );
}

/**
 * Right-hand chat rail on the PuppyGarden board. Boots the per-user task
 * secretary session via the server and keeps it out of the main sidebar list.
 */
export function PuppyGardenChatSidebar() {
  const {
    data: profile,
    isLoading: profileLoading,
    isError: profileError,
    error: profileErrorDetail,
    refetch: reloadProfile,
  } = useSecretaryProfile();
  const {
    data: session,
    isLoading: sessionLoading,
    isError: sessionError,
    error: sessionErrorDetail,
    refetch: reloadSession,
  } = useSecretarySession();
  const resetSession = useResetSecretarySession();
  const [resetOpen, setResetOpen] = useState(false);

  const sessionId = session?.conversation_id ?? profile?.conversation_id ?? null;
  const bootstrapError =
    profileError && profileErrorDetail instanceof Error
      ? profileErrorDetail.message
      : sessionError && sessionErrorDetail instanceof Error
        ? sessionErrorDetail.message
        : null;

  const handleReload = useCallback(() => {
    void reloadProfile();
    void reloadSession();
  }, [reloadProfile, reloadSession]);

  const handleReset = useCallback(() => {
    resetSession.mutate(undefined, {
      onSuccess: () => setResetOpen(false),
    });
  }, [resetSession]);

  return (
    <aside
      className="relative z-10 flex h-full min-h-0 min-w-[300px] flex-col border-border border-l bg-background"
      data-testid="puppy-garden-chat-sidebar"
    >
      {profileLoading || sessionLoading ? (
        <div className="flex flex-1 items-center justify-center gap-2 text-muted-foreground text-sm">
          <Loader2Icon className="size-4 animate-spin" />
          Loading secretary…
        </div>
      ) : bootstrapError ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 p-4 text-center text-muted-foreground text-sm">
          <p>{bootstrapError}</p>
          <Button type="button" variant="outline" size="sm" onClick={handleReload}>
            Retry
          </Button>
        </div>
      ) : sessionId == null ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 p-4 text-center text-muted-foreground text-sm">
          <p>Could not load the task secretary session.</p>
          <Button type="button" variant="outline" size="sm" onClick={handleReload}>
            Retry
          </Button>
        </div>
      ) : (
        <>
          <PuppyGardenSessionView
            sessionId={sessionId}
            onReload={handleReload}
            onReset={() => setResetOpen(true)}
            resetPending={resetSession.isPending}
          />
          <Dialog open={resetOpen} onOpenChange={setResetOpen}>
            <DialogContent aria-describedby="puppy-garden-reset-description">
              <DialogHeader>
                <DialogTitle>Reset task secretary?</DialogTitle>
                <DialogDescription id="puppy-garden-reset-description">
                  This deletes the current secretary chat and starts a fresh session seeded with
                  the secretary manual.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setResetOpen(false)}>
                  Cancel
                </Button>
                <Button
                  type="button"
                  variant="destructive"
                  onClick={handleReset}
                  disabled={resetSession.isPending}
                  data-testid="puppy-garden-secretary-reset-confirm"
                >
                  {resetSession.isPending ? (
                    <>
                      <Loader2Icon className="size-4 animate-spin" />
                      Resetting…
                    </>
                  ) : (
                    "Reset session"
                  )}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </>
      )}
    </aside>
  );
}
