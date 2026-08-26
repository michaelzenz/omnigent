import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDownIcon, Loader2Icon, Maximize2Icon, RotateCcwIcon, XIcon } from "lucide-react";
import { useNavigate } from "@/lib/routing";
import { buildBubbles, createBubbleCache, type Bubble } from "@/lib/renderItems";
import { getCurrentAuthorId } from "@/lib/identity";
import { isCostRoutingSession } from "@/components/CostRoutingControl";
import { useServerInfo } from "@/lib/CapabilitiesContext";
import { useOmniHarnessModelOptions } from "@/hooks/useModelSettings";
import { EMPTY_OMNIHARNESS_MODEL_OPTIONS } from "@/lib/omniharnessModels";
import { type Agent, useAgents, useSessionAgent } from "@/hooks/useAgents";
import {
  useBrokerProfile,
  useBrokerSession,
  useResetBrokerSession,
  useResetSecretarySession,
  useSecretaryProfile,
  useSecretarySession,
} from "@/hooks/useAgentTasks";
import { useRefreshSessionStateOnRunnerOnline } from "@/hooks/useSessionOnlineRefresh";
import { useSessionRunnerOnline, useRunnerHealthRegistration } from "@/hooks/RunnerHealthProvider";
import { livenessRowFromSession, useSessionLiveness } from "@/hooks/useSessionLiveness";
import { useSession } from "@/hooks/useSession";
import { useConversations } from "@/hooks/useConversations";
import { useMarkConversationSeen } from "@/hooks/useUnseenConversations";
import {
  buildPendingBubbles,
  ComposerSdkModelSelect,
  computeIsWorking,
  computeShowsWorking,
  effortLevelsForConv,
  MainAgentSurface,
  mergePendingBubbles,
  modelPickerKindForConv,
  readOnlyReasonForSessionLabels,
  reorderCommittedRequestElicitations,
  shouldQueueSend,
  shouldShowCodexPlanModeControl,
  shouldShowEffortPicker,
  shouldShowGoalControl,
  subAgentComposerLabel,
  supportsSessionProfileSelection,
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  type PuppyGardenChatTarget,
  type PuppyGardenRole,
  usePuppyGardenChat,
} from "./puppyGarden/PuppyGardenChatContext";

interface PuppyGardenSessionViewProps {
  sessionId: string;
}

/**
 * Full session view for the PuppyGarden sidebar. Reuses MainAgentSurface so
 * cursor-native elicitation cards, send queueing, and composer props match
 * the main chat page.
 */
function PuppyGardenSessionView({ sessionId }: PuppyGardenSessionViewProps) {
  const {
    data: agents,
    isLoading: agentsLoading,
    error: agentsError,
    refetch: refetchAgents,
  } = useAgents();
  const { data: boundAgentBySession } = useSessionAgent(sessionId);
  const { session: activeSession, isLoading: sessionLoading } = useSession(sessionId);
  const runnerHealthSessions = useMemo(() => [{ id: sessionId }], [sessionId]);
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
  const sdkModelOptions = useOmniHarnessModelOptions().data ?? EMPTY_OMNIHARNESS_MODEL_OPTIONS;

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
      if (
        chat.busySendMode === "queue" &&
        shouldQueueSend(chat.conversationId, chat.status, chat.sessionStatus, chat.queuedMessages)
      ) {
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
  const capabilitySource = {
    labels: activeSessionLabels ?? {},
    harness: activeSession?.harness ?? null,
  };
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

  const showProfileSelector = supportsSessionProfileSelection(activeSession);
  const profileControls = showProfileSelector ? (
    <ComposerSdkModelSelect
      sdkModelOptions={sdkModelOptions}
      costRoutingEligible={costRoutingEligible}
      disabled={permissionLevel === 1 || readOnlyReason !== null}
    />
  ) : null;

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
          sdkModelOptions={sdkModelOptions}
          profileControls={profileControls}
          hideExecutionTargetQuickSelect
          showCodexPlanMode={shouldShowCodexPlanModeControl(capabilitySource)}
          showGoalControl={shouldShowGoalControl(capabilitySource)}
          costRoutingEligible={costRoutingEligible}
          subAgentLabel={subAgentLabel}
        />
      </div>
    </TerminalFirstContextProvider>
  );
}

function dockTitle(target: PuppyGardenChatTarget): string {
  if (target.kind === "role") {
    return target.role === "broker" ? "Task broker" : "Task secretary";
  }
  if (target.kind === "manager") {
    return `${target.title} · Manager`;
  }
  return `${target.label} · Worker`;
}

function emptyDockMessage(target: PuppyGardenChatTarget): string {
  if (target.kind === "manager") {
    return "No manager session yet. Bootstrap the task manager before chatting here.";
  }
  return "No live worker session. Dispatch work to this lane first.";
}

interface RoleSessionBootstrap {
  sessionId: string | null;
  loading: boolean;
  error: string | null;
  onReload: () => void;
  resetPending: boolean;
  resetError: string | null;
  onReset: () => void;
}

function useRoleSessionBootstrap(role: PuppyGardenRole): RoleSessionBootstrap {
  const secretaryProfile = useSecretaryProfile();
  const secretarySession = useSecretarySession();
  const brokerProfile = useBrokerProfile();
  const brokerSession = useBrokerSession();
  const resetSecretary = useResetSecretarySession();
  const resetBroker = useResetBrokerSession();

  const profile = role === "broker" ? brokerProfile : secretaryProfile;
  const session = role === "broker" ? brokerSession : secretarySession;
  const reset = role === "broker" ? resetBroker : resetSecretary;

  const sessionId = session.data?.conversation_id ?? profile.data?.conversation_id ?? null;
  const errorDetail = profile.error ?? session.error;
  const bootstrapError =
    errorDetail instanceof Error ? errorDetail.message : errorDetail ? String(errorDetail) : null;

  const onReload = useCallback(() => {
    void profile.refetch();
    void session.refetch();
  }, [profile, session]);

  const onReset = useCallback(() => {
    reset.mutate();
  }, [reset]);

  const resetErrorDetail = reset.error;
  const resetError =
    resetErrorDetail instanceof Error
      ? resetErrorDetail.message
      : resetErrorDetail
        ? String(resetErrorDetail)
        : null;

  return {
    sessionId,
    loading: profile.isLoading || session.isLoading,
    error: bootstrapError,
    onReload,
    resetPending: reset.isPending,
    resetError,
    onReset,
  };
}

/**
 * Right-hand chat dock on the PuppyGarden board. Shows per-user role chats
 * (secretary/broker) by default; task manager and worker lanes override it
 * when selected on the board.
 */
export function PuppyGardenChatSidebar() {
  const navigate = useNavigate();
  const { target, setRole, dismissToRole } = usePuppyGardenChat();
  const [resetOpen, setResetOpen] = useState(false);

  const secretaryBootstrap = useRoleSessionBootstrap("secretary");
  const brokerBootstrap = useRoleSessionBootstrap("broker");
  const activeRoleBootstrap =
    target.kind === "role" && target.role === "broker" ? brokerBootstrap : secretaryBootstrap;

  const conversationId = useMemo(() => {
    if (target.kind === "role") {
      return target.role === "broker" ? brokerBootstrap.sessionId : secretaryBootstrap.sessionId;
    }
    return target.conversationId;
  }, [target, brokerBootstrap.sessionId, secretaryBootstrap.sessionId]);

  // The dock IS the reading surface for role/manager/worker chats — they're
  // not viewed via /c/<id>, so the sidebar's active-row suppression doesn't
  // fire. Mark the dock's active conversation seen (mirroring ChatPage's
  // useMarkConversationSeen) so the sidebar unread dot clears while you're
  // looking at it here. The broker is exempt from the dot entirely (Part A),
  // but marking it seen here is harmless.
  const { data: conversationsData } = useConversations("", true);
  const dockUpdatedAt = useMemo(
    () =>
      conversationsData?.pages.flatMap((p) => p.data).find((c) => c.id === conversationId)
        ?.updated_at,
    [conversationsData, conversationId],
  );
  useMarkConversationSeen(conversationId ?? undefined, dockUpdatedAt);

  const title = dockTitle(target);
  const showRoleControls = target.kind === "role";
  const showDismiss = target.kind !== "role";
  const showEmptyState = target.kind !== "role" && conversationId == null;

  const handleFullscreen = useCallback(() => {
    if (conversationId == null) return;
    navigate(`/c/${conversationId}`, { state: { returnTo: "/puppy-garden" } });
  }, [conversationId, navigate]);

  const handleResetConfirm = useCallback(() => {
    activeRoleBootstrap.onReset();
  }, [activeRoleBootstrap]);

  // Auto-close the reset dialog once the mutation settles successfully.
  const prevResetPending = useRef(false);
  useEffect(() => {
    if (
      prevResetPending.current &&
      !activeRoleBootstrap.resetPending &&
      !activeRoleBootstrap.resetError
    ) {
      setResetOpen(false);
    }
    prevResetPending.current = activeRoleBootstrap.resetPending;
  }, [activeRoleBootstrap.resetPending, activeRoleBootstrap.resetError]);

  return (
    <aside
      className="relative z-10 flex h-full min-h-0 min-w-[300px] flex-col border-border border-l bg-background"
      data-testid="puppy-garden-chat-sidebar"
    >
      <div className="flex shrink-0 items-center gap-1 border-border border-b px-2 py-1.5">
        {target.kind === "role" ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 min-w-0 flex-1 justify-start gap-1 px-2 font-medium text-sm"
                data-testid="puppy-garden-chat-title"
              >
                <span className="truncate">{title}</span>
                <ChevronDownIcon className="size-3.5 shrink-0 text-muted-foreground" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start">
              <DropdownMenuItem
                onSelect={() => setRole("secretary")}
                data-testid="puppy-garden-chat-role-secretary"
              >
                Task secretary
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() => setRole("broker")}
                data-testid="puppy-garden-chat-role-broker"
              >
                Task broker
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : (
          <span
            className="min-w-0 flex-1 truncate px-2 font-medium text-sm"
            data-testid="puppy-garden-chat-title"
          >
            {title}
          </span>
        )}

        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-7 shrink-0 text-muted-foreground"
          onClick={handleFullscreen}
          disabled={conversationId == null}
          aria-label="Open in full screen"
          data-testid="puppy-garden-chat-fullscreen"
        >
          <Maximize2Icon className="size-3.5" />
        </Button>

        {showRoleControls ? (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7 shrink-0 text-muted-foreground"
            onClick={() => setResetOpen(true)}
            disabled={activeRoleBootstrap.resetPending}
            aria-label="Reset session"
            data-testid="puppy-garden-chat-reset"
          >
            {activeRoleBootstrap.resetPending ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <RotateCcwIcon className="size-3.5" />
            )}
          </Button>
        ) : null}

        {showDismiss ? (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7 shrink-0 text-muted-foreground"
            onClick={dismissToRole}
            aria-label="Close task chat"
            data-testid="puppy-garden-chat-dismiss"
          >
            <XIcon className="size-3.5" />
          </Button>
        ) : null}
      </div>

      {showEmptyState ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 p-4 text-center text-muted-foreground text-sm">
          <p>{emptyDockMessage(target)}</p>
        </div>
      ) : target.kind === "role" && activeRoleBootstrap.loading ? (
        <div className="flex flex-1 items-center justify-center gap-2 text-muted-foreground text-sm">
          <Loader2Icon className="size-4 animate-spin" />
          Loading…
        </div>
      ) : target.kind === "role" && activeRoleBootstrap.error ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 p-4 text-center text-muted-foreground text-sm">
          <p>{activeRoleBootstrap.error}</p>
          <Button type="button" variant="outline" size="sm" onClick={activeRoleBootstrap.onReload}>
            Retry
          </Button>
        </div>
      ) : conversationId == null ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 p-4 text-center text-muted-foreground text-sm">
          <p>Could not load the session.</p>
          {target.kind === "role" ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={activeRoleBootstrap.onReload}
            >
              Retry
            </Button>
          ) : null}
        </div>
      ) : (
        <PuppyGardenSessionView key={conversationId} sessionId={conversationId} />
      )}

      {showRoleControls ? (
        <Dialog open={resetOpen} onOpenChange={setResetOpen}>
          <DialogContent aria-describedby="puppy-garden-reset-description">
            <DialogHeader>
              <DialogTitle>
                Reset task{" "}
                {target.kind === "role" && target.role === "broker" ? "broker" : "secretary"}?
              </DialogTitle>
              <DialogDescription id="puppy-garden-reset-description">
                This deletes the current chat and starts a fresh session seeded with the role
                manual.
              </DialogDescription>
              {activeRoleBootstrap.resetError ? (
                <p className="text-sm text-destructive" data-testid="puppy-garden-chat-reset-error">
                  {activeRoleBootstrap.resetError}
                </p>
              ) : null}
            </DialogHeader>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setResetOpen(false)}>
                Cancel
              </Button>
              <Button
                type="button"
                variant="destructive"
                onClick={handleResetConfirm}
                disabled={activeRoleBootstrap.resetPending}
                data-testid="puppy-garden-chat-reset-confirm"
              >
                {activeRoleBootstrap.resetPending ? (
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
      ) : null}
    </aside>
  );
}
