import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Conversation,
  ConversationContent,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import { Loader2Icon } from "lucide-react";
import { buildBubbles, createBubbleCache, type Bubble } from "@/lib/renderItems";
import {
  buildPendingBubbles,
  BubbleView,
  computeIsWorking,
  Composer,
  mergePendingBubbles,
} from "@/pages/ChatPage";
import { getCurrentAuthorId } from "@/lib/identity";
import {
  consumePendingInitialPrompt,
  useChatStore,
  type PendingInitialPrompt,
} from "@/store/chatStore";
import { useAgents } from "@/hooks/useAgents";
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
 * Full session view for the PuppyGarden sidebar. Renders the shared chatStore's
 * transcript and composer for the board session, and resets when the session is
 * deleted (conversationLoadError on the store).
 */
function PuppyGardenSessionView({ sessionId, pendingPrompt, onReset }: PuppyGardenSessionViewProps) {
  const { data: agents, isLoading: agentsLoading } = useAgents();

  const blocks = useChatStore((s) => s.blocks);
  const pendingUserMessages = useChatStore((s) => s.pendingUserMessages);
  const activeResponse = useChatStore((s) => s.activeResponse);
  const interruptedResponseIds = useChatStore((s) => s.interruptedResponseIds);
  const loadingConversation = useChatStore((s) => s.loadingConversation);
  const conversationLoadError = useChatStore((s) => s.conversationLoadError);
  const boundAgentId = useChatStore((s) => s.boundAgentId);
  const status = useChatStore((s) => s.status);
  const sessionStatus = useChatStore((s) => s.sessionStatus);

  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const bubbleCacheRef = useRef(createBubbleCache());
  const [replyQuotes] = useState<string[]>([]);
  // Prevents double-send across StrictMode's setup→cleanup→setup double-invoke.
  const autoSentRef = useRef(false);

  // Bind chatStore to this session.
  useEffect(() => {
    void useChatStore.getState().switchTo(sessionId);
  }, [sessionId]);

  // If the session was deleted, the store will surface a load error — reset.
  useEffect(() => {
    if (conversationLoadError) onReset();
  }, [conversationLoadError, onReset]);

  const agentId = selectedAgentId ?? boundAgentId ?? agents?.[0]?.id ?? null;

  // Auto-send the initial prompt once the session stream is ready. The prompt is
  // consumed in the parent before this component mounts so it's passed directly
  // as a prop — no store read needed, no timing race with the Map deletion.
  useEffect(() => {
    if (!pendingPrompt || autoSentRef.current) return;
    if (loadingConversation || !agentId) return;
    autoSentRef.current = true;
    const store = useChatStore.getState();
    if (pendingPrompt.skill) {
      void store.sendSlashCommand(pendingPrompt.skill.name, pendingPrompt.skill.args, agentId);
    } else {
      void store.send(pendingPrompt.text, agentId, pendingPrompt.files ?? []);
    }
  }, [pendingPrompt, loadingConversation, agentId]);

  const isWorking = computeIsWorking(sessionStatus);

  const bubbles = useMemo<Bubble[]>(() => {
    const committed = buildBubbles(
      blocks,
      activeResponse,
      bubbleCacheRef.current,
      interruptedResponseIds,
    );
    if (pendingUserMessages.length === 0) return committed;
    return mergePendingBubbles(
      committed,
      buildPendingBubbles(pendingUserMessages, getCurrentAuthorId()),
    );
  }, [blocks, activeResponse, interruptedResponseIds, pendingUserMessages]);

  const onSend = useCallback(
    (text: string, files?: File[]) => {
      if (!agentId) return;
      void useChatStore.getState().send(text, agentId, files);
    },
    [agentId],
  );

  const onStop = useCallback(() => {
    useChatStore.getState().stop();
  }, []);

  if (loadingConversation) {
    return (
      <div className="flex flex-1 items-center justify-center gap-2 text-muted-foreground text-sm">
        <Loader2Icon className="size-4 animate-spin" />
        Loading…
      </div>
    );
  }

  return (
    <>
      <div className="relative flex min-h-0 flex-1 overflow-hidden">
        <Conversation className="chat-scroll-fade flex-1">
          <ConversationContent className="mx-auto w-full gap-4 pt-20 pb-6">
            {bubbles.map((bubble) => {
              const key =
                bubble.kind === "user"
                  ? (bubble.stableKey ?? bubble.itemId)
                  : bubble.kind === "assistant"
                    ? bubble.stableId
                    : bubble.kind === "routing_decision"
                      ? bubble.itemId
                      : bubble.kind;
              return <BubbleView key={key} bubble={bubble} />;
            })}
          </ConversationContent>
          <ConversationScrollButton />
        </Conversation>
      </div>
      <Composer
        status={status}
        isWorking={isWorking}
        disabled={!agentId}
        onSend={onSend}
        onStop={onStop}
        agents={agents}
        agentsLoading={agentsLoading}
        selectedAgentId={agentId}
        onSelectAgent={setSelectedAgentId}
        permissionLevel={2}
        readOnlyReason={null}
        replyQuotes={replyQuotes}
        onRemoveQuote={() => {}}
        onClearAllQuotes={() => {}}
        effortLevels={[]}
        showEffort={false}
        showModels={false}
        modelPickerKind={null}
        codexModelOptions={[]}
        showCodexPlanMode={false}
      />
    </>
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
    // Consume the pending prompt immediately — before React re-renders and mounts
    // PuppyGardenSessionView — so the session view receives it as a stable prop
    // rather than racing to read from the store map.
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
