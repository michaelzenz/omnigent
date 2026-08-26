import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import {
  CheckIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  Loader2Icon,
  RotateCcwIcon,
  Trash2Icon,
} from "lucide-react";
import { FilePathAwareMessageResponse } from "@/components/blocks/ChatMarkdown";
import { Button } from "@/components/ui/button";
import {
  useAgentTextThreads,
  useCreateAgentTextThread,
  useCreateAgentTextThreadTurn,
  useDeleteAgentTextThread,
  useResolveAgentTextThread,
  useRetryAgentTextThread,
  useRetryAgentTextThreadTurn,
  type AgentTextThread,
  type AgentTextThreadTurn,
  type AgentTextThreadView,
} from "@/hooks/useAgentTextThreads";
import { isSendMessageShortcut } from "@/lib/sendMessagePreferences";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chatStore";
import type { AgentTextCommentsUI } from "./AgentTextCommentsContext";

const EMPTY_THREADS: AgentTextThread[] = [];

function assistantText(source: Pick<AgentTextThread | AgentTextThreadTurn, "items">): string {
  const parts: string[] = [];
  for (const item of source.items) {
    if (item.type !== "message" || item.role !== "assistant" || !Array.isArray(item.content))
      continue;
    for (const block of item.content) {
      if (block && typeof block === "object" && "text" in block && typeof block.text === "string") {
        parts.push(block.text);
      }
    }
  }
  return parts.join("\n");
}

function Quote({ text }: { text: string }) {
  return (
    <blockquote className="line-clamp-3 border-l-2 border-purple-400 pl-2 text-xs text-muted-foreground">
      {text}
    </blockquote>
  );
}

function ThreadDraftEditor({
  anchorText,
  body,
  canEdit,
  isPending,
  textareaRef,
  onBodyChange,
  onCancel,
  onSave,
}: {
  anchorText: string;
  body: string;
  canEdit: boolean;
  isPending: boolean;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  onBodyChange: (body: string) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  return (
    <div
      data-agent-thread-draft
      className="z-10 space-y-2 rounded-lg border border-purple-400 bg-background p-3 shadow-sm"
    >
      <Quote text={anchorText} />
      <textarea
        ref={textareaRef}
        rows={4}
        value={body}
        placeholder="Ask about this response…"
        className="w-full resize-none rounded-md border border-border bg-background px-2 py-1.5 text-sm outline-none focus:border-ring"
        onChange={(event) => onBodyChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape" && !body) onCancel();
          if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
            event.preventDefault();
            onSave();
          }
        }}
      />
      <div className="flex justify-end gap-2">
        <Button size="xs" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="xs" disabled={!canEdit || !body.trim() || isPending} onClick={onSave}>
          {isPending && <Loader2Icon className="size-3 animate-spin" />}
          Send comment
        </Button>
      </div>
    </div>
  );
}

function FollowUpComposer({
  value,
  placeholder = "Ask a follow-up…",
  disabled,
  autoFocus = false,
  onChange,
  onSend,
}: {
  value: string;
  placeholder?: string;
  disabled: boolean;
  autoFocus?: boolean;
  onChange: (value: string) => void;
  onSend: () => void;
}) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const resize = (element: HTMLTextAreaElement) => {
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 112)}px`;
  };
  useEffect(() => {
    if (textareaRef.current) resize(textareaRef.current);
  }, [value]);
  return (
    <div className="flex items-end gap-1.5 border-t border-border bg-background p-2">
      <textarea
        ref={textareaRef}
        rows={1}
        value={value}
        autoFocus={autoFocus}
        disabled={disabled}
        placeholder={placeholder}
        className="max-h-28 min-h-8 flex-1 resize-none overflow-y-auto rounded-md border border-border bg-background px-2 py-1.5 text-sm outline-none focus:border-ring"
        onChange={(event) => {
          onChange(event.target.value);
          resize(event.target);
        }}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing || !isSendMessageShortcut(event)) return;
          event.preventDefault();
          onSend();
        }}
      />
      <Button
        size="icon-xs"
        aria-label="Send follow-up"
        disabled={disabled || !value.trim()}
        onClick={onSend}
      >
        <span aria-hidden>↑</span>
      </Button>
    </div>
  );
}

function ThreadTurnView({
  turn,
  answer,
  isStreaming,
  canEdit,
  retrying,
  onRetry,
}: {
  turn: AgentTextThreadTurn;
  answer: string;
  isStreaming: boolean;
  canEdit: boolean;
  retrying: boolean;
  onRetry: () => void;
}) {
  return (
    <div className="space-y-2 border-t border-border pt-3">
      <div className="space-y-1">
        <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">You</p>
        {turn.selected_quote && <Quote text={turn.selected_quote} />}
        <p className="whitespace-pre-wrap text-sm">{turn.question}</p>
      </div>
      <div className="pl-2">
        {turn.state === "initializing" ? (
          <p className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2Icon className="size-3 animate-spin" /> Initializing…
          </p>
        ) : turn.state === "queued" || turn.state === "submitting" ? (
          <p className="text-xs text-muted-foreground">Queued</p>
        ) : turn.state === "failed" ? (
          <div className="flex items-center justify-between gap-2 text-xs text-destructive">
            <span>{turn.failure_message ?? "Could not send follow-up."}</span>
            {canEdit && (
              <Button size="xs" variant="ghost" disabled={retrying} onClick={onRetry}>
                Retry
              </Button>
            )}
          </div>
        ) : (
          <div
            data-agent-thread-response={turn.state === "answered" ? turn.thread_id : undefined}
            className="space-y-1"
          >
            {answer && <FilePathAwareMessageResponse>{answer}</FilePathAwareMessageResponse>}
            {isStreaming && (
              <p className="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2Icon className="size-3 animate-spin" /> Agent is answering…
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

interface QuoteDraft {
  threadId: string;
  quote: string;
  x: number;
  y: number;
  editing: boolean;
}

function quoteOverlayPosition(rect: DOMRect): { x: number; y: number } {
  const margin = 12;
  const width = Math.min(320, Math.max(0, window.innerWidth - margin * 2));
  const height = Math.min(190, Math.max(0, window.innerHeight - margin * 2));
  return {
    x: Math.max(margin, Math.min(rect.left, window.innerWidth - width - margin)),
    y: Math.max(margin, Math.min(rect.bottom + 8, window.innerHeight - height - margin)),
  };
}

export function AgentTextThreadPanel({
  conversationId,
  canEdit,
  ui,
}: {
  conversationId: string;
  canEdit: boolean;
  ui: AgentTextCommentsUI;
}) {
  const [view, setView] = useState<AgentTextThreadView>("open");
  const draftBody = ui.draftBody;
  const [error, setError] = useState<string | null>(null);
  const [followupDrafts, setFollowupDrafts] = useState<Record<string, string>>({});
  const [quoteDraft, setQuoteDraft] = useState<QuoteDraft | null>(null);
  const [quoteBody, setQuoteBody] = useState("");
  const draftRequestIdRef = useRef<string>(crypto.randomUUID());
  const restoredDraftRequestIdRef = useRef<string | null>(null);
  const followupRequestIdsRef = useRef(new Map<string, { signature: string; requestId: string }>());
  const draftRef = useRef<HTMLTextAreaElement | null>(null);
  const pendingAnchorRef = useRef(ui.pendingAnchor);
  pendingAnchorRef.current = ui.pendingAnchor;
  const query = useAgentTextThreads(conversationId, view);
  const openQuery = useAgentTextThreads(conversationId, "open");
  const create = useCreateAgentTextThread(conversationId);
  const resolve = useResolveAgentTextThread(conversationId);
  const retry = useRetryAgentTextThread(conversationId);
  const remove = useDeleteAgentTextThread(conversationId);
  const liveBlocks = useChatStore((state) => state.blocks);
  const activeResponse = useChatStore((state) => state.activeResponse);
  const threads = query.data ?? EMPTY_THREADS;
  const activeThread = useMemo(
    () => threads.find((thread) => thread.id === ui.activeCommentId) ?? null,
    [threads, ui.activeCommentId],
  );
  const activeThreadIdRef = useRef(activeThread?.id ?? null);
  activeThreadIdRef.current = activeThread?.id ?? null;
  const followupBody = activeThread ? (followupDrafts[activeThread.id] ?? "") : "";
  const setFollowupBody = (body: string) => {
    if (!activeThread) return;
    setFollowupDrafts((current) => ({ ...current, [activeThread.id]: body }));
  };
  const createTurn = useCreateAgentTextThreadTurn(conversationId, activeThread?.id ?? "");
  const retryTurn = useRetryAgentTextThreadTurn(conversationId, activeThread?.id ?? "");
  const liveAnswers = useMemo(() => {
    const result = new Map<string, string>();
    const collect = (id: string, responseId: string | null) => {
      if (!responseId) return;
      const responseBlocks = liveBlocks.filter((block) => block.ctx.responseId === responseId);
      const completed = responseBlocks.flatMap((block) =>
        block.type === "text_done" ? [block.fullText] : [],
      );
      const text =
        completed.length > 0
          ? completed.join("\n")
          : responseBlocks
              .flatMap((block) => (block.type === "text_chunk" ? [block.text] : []))
              .join("");
      if (text) result.set(id, text);
    };
    for (const thread of threads) {
      collect(thread.id, thread.response_id);
      for (const turn of thread.turns) collect(turn.id, turn.response_id);
    }
    return result;
  }, [liveBlocks, threads]);
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const processingCount = threads.reduce(
    (count, thread) =>
      count +
      (thread.state === "initializing" || thread.state === "queued" || thread.state === "running"
        ? 1
        : 0) +
      thread.turns.filter(
        (turn) =>
          turn.state === "initializing" ||
          turn.state === "queued" ||
          turn.state === "submitting" ||
          turn.state === "running",
      ).length,
    0,
  );
  const activeThreadIndex = threads.findIndex((thread) => thread.id === ui.activeCommentId);
  const navigateThread = (direction: -1 | 1) => {
    if (threads.length === 0) return;
    const nextIndex =
      activeThreadIndex === -1
        ? direction === -1
          ? threads.length - 1
          : 0
        : activeThreadIndex + direction;
    const thread = threads[nextIndex];
    if (thread) ui.activateComment(thread.id);
  };
  const canNavigatePrevious = threads.length > 0 && activeThreadIndex !== 0;
  const canNavigateNext = threads.length > 0 && activeThreadIndex !== threads.length - 1;

  useEffect(() => {
    draftRequestIdRef.current = restoredDraftRequestIdRef.current ?? crypto.randomUUID();
    restoredDraftRequestIdRef.current = null;
    setError(null);
  }, [ui.pendingAnchor]);

  useEffect(() => {
    if (!quoteDraft) return;
    const close = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Element && target.closest("[data-thread-quote-overlay]")) return;
      setQuoteDraft(null);
    };
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, [quoteDraft]);

  useEffect(() => {
    if (!ui.pendingAnchor) return;
    const frame = requestAnimationFrame(() => draftRef.current?.focus({ preventScroll: true }));
    return () => cancelAnimationFrame(frame);
  }, [ui.pendingAnchor]);

  const saveDraft = async () => {
    const anchor = ui.pendingAnchor;
    const comment = draftBody.trim();
    if (!anchor || !comment) return;
    const requestId = draftRequestIdRef.current;
    setError(null);

    // Start the request before closing the draft so its optimistic card
    // replaces the editor without an empty intermediate state.
    const request = create.mutateAsync({
      ...anchor,
      client_request_id: requestId,
      comment,
    });
    ui.cancelDraft();
    try {
      await request;
    } catch (cause) {
      if (
        useChatStore.getState().conversationId === conversationId &&
        pendingAnchorRef.current === null
      ) {
        restoredDraftRequestIdRef.current = requestId;
        ui.openDraft(anchor);
        ui.setDraftBody(comment);
      }
      setError(cause instanceof Error ? cause.message : "Could not create threaded comment.");
    }
  };

  const openCount = openQuery.data?.length ?? 0;
  useEffect(() => {
    setQuoteDraft(null);
    setQuoteBody("");
  }, [activeThread?.id]);

  useEffect(() => {
    if (!activeThread) return;
    document
      .querySelector<HTMLElement>(`[data-agent-thread-card="${CSS.escape(activeThread.id)}"]`)
      ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [activeThread]);

  const sendFollowup = async (question: string, selectedQuote: string | null = null) => {
    if (!activeThread || !question.trim()) return;
    const threadId = activeThread.id;
    const trimmedQuestion = question.trim();
    const signature = `${trimmedQuestion}\u0000${selectedQuote ?? ""}`;
    const previousRequest = followupRequestIdsRef.current.get(threadId);
    const requestId =
      previousRequest?.signature === signature ? previousRequest.requestId : crypto.randomUUID();
    followupRequestIdsRef.current.set(threadId, { signature, requestId });
    setFollowupBody("");
    setQuoteBody("");
    setQuoteDraft(null);
    window.getSelection()?.removeAllRanges();
    try {
      await createTurn.mutateAsync({
        client_request_id: requestId,
        question: trimmedQuestion,
        selected_quote: selectedQuote,
      });
      const currentRequest = followupRequestIdsRef.current.get(threadId);
      if (currentRequest?.requestId === requestId) followupRequestIdsRef.current.delete(threadId);
    } catch (cause) {
      if (
        useChatStore.getState().conversationId !== conversationId ||
        activeThreadIdRef.current !== threadId
      ) {
        return;
      }
      if (selectedQuote) {
        setQuoteBody(question);
        setQuoteDraft({
          threadId,
          quote: selectedQuote,
          ...quoteOverlayPosition(new DOMRect(16, 16, 0, 0)),
          editing: true,
        });
      } else {
        setFollowupBody(question);
      }
      setError(cause instanceof Error ? cause.message : "Could not send follow-up.");
    }
  };

  const captureThreadSelection = (event: ReactMouseEvent<HTMLElement>) => {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.toString().trim()) return;
    const anchorElement =
      selection.anchorNode instanceof Element
        ? selection.anchorNode
        : selection.anchorNode?.parentElement;
    const focusElement =
      selection.focusNode instanceof Element
        ? selection.focusNode
        : selection.focusNode?.parentElement;
    const anchorResponse = anchorElement?.closest<HTMLElement>("[data-agent-thread-response]");
    const focusResponse = focusElement?.closest<HTMLElement>("[data-agent-thread-response]");
    if (
      !anchorResponse ||
      anchorResponse !== focusResponse ||
      !event.currentTarget.contains(anchorResponse)
    ) {
      return;
    }
    const rect = selection.getRangeAt(0).getBoundingClientRect();
    setQuoteBody("");
    setQuoteDraft({
      threadId: anchorResponse.dataset.agentThreadResponse ?? "",
      quote: selection.toString().trim(),
      ...quoteOverlayPosition(rect),
      editing: false,
    });
  };

  return (
    <section className="flex min-h-0 flex-1 flex-col" aria-label="Threaded agent comments">
      <div className="flex shrink-0 gap-1 border-b border-border px-3 py-2">
        <Button
          size="xs"
          variant={view === "open" ? "secondary" : "ghost"}
          onClick={() => setView("open")}
        >
          Open {openCount > 0 ? openCount : ""}
        </Button>
        <Button
          size="xs"
          variant={view === "resolved" ? "secondary" : "ghost"}
          onClick={() => setView("resolved")}
        >
          Resolved
        </Button>
      </div>
      <div className="relative min-h-0 flex-1">
        <div
          ref={scrollerRef}
          className={cn(
            "h-full overflow-y-auto [overflow-anchor:none]",
            threads.length > 0 && "pb-16",
          )}
          data-agent-thread-scroller
          onClick={(event) => {
            const target = event.target;
            if (
              !(target instanceof Element) ||
              !target.closest("[data-agent-thread-card], [data-agent-thread-draft]")
            ) {
              ui.activateComment(null);
            }
          }}
        >
          {query.isError ? (
            <div className="flex flex-col items-center gap-2 p-8 text-sm text-destructive">
              Could not load threaded replies.
              <Button size="xs" variant="ghost" onClick={() => query.refetch()}>
                Retry
              </Button>
            </div>
          ) : query.isLoading ? (
            <div className="flex justify-center p-8 text-sm text-muted-foreground">Loading…</div>
          ) : threads.length === 0 && !ui.pendingAnchor ? (
            <div className="p-8 text-center text-sm text-muted-foreground">
              {view === "open"
                ? "Select completed agent text and choose Comment."
                : "No resolved threads."}
            </div>
          ) : (
            <div className="space-y-3 p-3" data-agent-thread-canvas>
              {ui.pendingAnchor && view === "open" && (
                <ThreadDraftEditor
                  anchorText={ui.pendingAnchor.selected_text}
                  body={draftBody}
                  canEdit={canEdit}
                  isPending={create.isPending}
                  textareaRef={draftRef}
                  onBodyChange={ui.setDraftBody}
                  onCancel={ui.cancelDraft}
                  onSave={() => void saveDraft()}
                />
              )}
              {threads.map((thread) => {
                const selected = ui.activeCommentId === thread.id;
                const answer = liveAnswers.get(thread.id) ?? assistantText(thread);
                const isStreaming =
                  thread.response_id !== null &&
                  activeResponse?.responseId === thread.response_id &&
                  activeResponse.state === "streaming";
                const hasPendingTurns = thread.turns.some(
                  (turn) =>
                    turn.state === "initializing" ||
                    turn.state === "queued" ||
                    turn.state === "submitting" ||
                    turn.state === "running",
                );
                return (
                  <article
                    key={thread.id}
                    data-agent-thread-card={thread.id}
                    className={cn(
                      "space-y-2 rounded-lg border bg-card p-3 text-sm",
                      selected &&
                        "flex max-h-[min(70vh,720px)] flex-col overflow-hidden border-purple-500 bg-purple-500/5",
                    )}
                    onClick={() => ui.activateComment(thread.id)}
                  >
                    <Quote text={thread.selected_text} />
                    <p className="whitespace-pre-wrap">{thread.user_comment}</p>
                    <div
                      className={cn(
                        "border-t border-border pt-2",
                        selected && "min-h-0 flex-1 overflow-y-auto [overflow-anchor:none]",
                      )}
                      onMouseUp={selected ? captureThreadSelection : undefined}
                    >
                      {thread.state === "initializing" ? (
                        <p className="flex items-center gap-2 text-xs text-muted-foreground">
                          <Loader2Icon className="size-3 animate-spin" />
                          Initializing…
                        </p>
                      ) : thread.state === "failed" ? (
                        <div className="space-y-2">
                          <p className="text-xs text-destructive">
                            {thread.failure_message ?? "Could not send this comment."}
                          </p>
                          {canEdit && (
                            <Button
                              size="xs"
                              variant="ghost"
                              onClick={(event) => {
                                event.stopPropagation();
                                retry.mutate({ id: thread.id });
                              }}
                            >
                              <RotateCcwIcon className="size-3" />
                              Retry
                            </Button>
                          )}
                        </div>
                      ) : isStreaming ? (
                        <div className="space-y-2">
                          {answer && (
                            <FilePathAwareMessageResponse>{answer}</FilePathAwareMessageResponse>
                          )}
                          <p className="flex items-center gap-2 text-xs text-muted-foreground">
                            <Loader2Icon className="size-3 animate-spin" />
                            Agent is answering…
                          </p>
                        </div>
                      ) : thread.state === "queued" ? (
                        <p className="text-xs text-muted-foreground">
                          Queued behind the current response…
                        </p>
                      ) : thread.state === "running" && !answer ? (
                        <p className="flex items-center gap-2 text-xs text-muted-foreground">
                          <Loader2Icon className="size-3 animate-spin" />
                          Agent is answering…
                        </p>
                      ) : selected ? (
                        <div data-agent-thread-response={thread.id}>
                          <FilePathAwareMessageResponse>
                            {answer || "Agent answered."}
                          </FilePathAwareMessageResponse>
                        </div>
                      ) : (
                        <p className="text-xs text-muted-foreground">
                          Agent answered · Click to expand
                        </p>
                      )}
                      {selected &&
                        thread.turns.map((turn) => {
                          const turnAnswer = liveAnswers.get(turn.id) ?? assistantText(turn);
                          const turnStreaming =
                            turn.response_id !== null &&
                            activeResponse?.responseId === turn.response_id &&
                            activeResponse.state === "streaming";
                          return (
                            <ThreadTurnView
                              key={turn.id}
                              turn={turn}
                              answer={turnAnswer}
                              isStreaming={turnStreaming}
                              canEdit={canEdit}
                              retrying={retryTurn.isPending}
                              onRetry={() => retryTurn.mutate(turn.id)}
                            />
                          );
                        })}
                    </div>
                    {canEdit && view === "open" && (
                      <div className="flex justify-end gap-1">
                        {thread.state === "answered" && !hasPendingTurns && (
                          <Button
                            size="xs"
                            variant="ghost"
                            onClick={(event) => {
                              event.stopPropagation();
                              resolve.mutate(
                                { id: thread.id },
                                { onSuccess: () => ui.activateComment(null) },
                              );
                            }}
                          >
                            <CheckIcon className="size-3" />
                            Resolve
                          </Button>
                        )}
                        {(thread.state === "failed" ||
                          (thread.state === "queued" && thread.response_id === null)) && (
                          <Button
                            size="icon-xs"
                            variant="ghost"
                            aria-label="Delete thread"
                            onClick={(event) => {
                              event.stopPropagation();
                              if (ui.activeCommentId === thread.id) ui.activateComment(null);
                              remove.mutate(thread.id);
                            }}
                          >
                            <Trash2Icon className="size-3.5" />
                          </Button>
                        )}
                      </div>
                    )}
                    {selected && canEdit && view === "open" && thread.state !== "failed" && (
                      <FollowUpComposer
                        value={followupBody}
                        disabled={false}
                        onChange={setFollowupBody}
                        onSend={() => void sendFollowup(followupBody)}
                      />
                    )}
                  </article>
                );
              })}
            </div>
          )}
        </div>
        {threads.length > 0 && (
          <div className="pointer-events-none absolute bottom-4 right-4 z-20 flex flex-col gap-1">
            <Button
              className="pointer-events-auto rounded-full bg-background shadow-sm"
              size="icon"
              type="button"
              variant="outline"
              aria-label="Previous comment"
              title="Previous comment"
              disabled={!canNavigatePrevious}
              onClick={() => navigateThread(-1)}
            >
              <ChevronUpIcon className="size-4" />
            </Button>
            <Button
              className="pointer-events-auto rounded-full bg-background shadow-sm"
              size="icon"
              type="button"
              variant="outline"
              aria-label="Next comment"
              title="Next comment"
              disabled={!canNavigateNext}
              onClick={() => navigateThread(1)}
            >
              <ChevronDownIcon className="size-4" />
            </Button>
          </div>
        )}
      </div>
      {error && (
        <p className="shrink-0 border-t border-border p-3 text-xs text-destructive">{error}</p>
      )}
      {processingCount > 0 && (
        <div
          className="flex shrink-0 items-center gap-2 border-t border-border bg-muted/30 px-3 py-1.5 text-[11px] text-muted-foreground"
          aria-live="polite"
        >
          <Loader2Icon className="size-3 animate-spin" />
          {processingCount} {processingCount === 1 ? "comment" : "comments"} being processed
        </div>
      )}
      {quoteDraft &&
        createPortal(
          <div
            data-thread-quote-overlay
            className={cn(
              "fixed z-[100]",
              quoteDraft.editing &&
                "max-h-[calc(100vh-1.5rem)] w-[min(20rem,calc(100vw-1.5rem))] overflow-y-auto rounded-lg border border-border bg-popover p-2 shadow-lg",
            )}
            style={{ left: quoteDraft.x, top: quoteDraft.y }}
            onMouseDown={(event) => event.stopPropagation()}
          >
            {quoteDraft.editing ? (
              <div className="space-y-2">
                <Quote text={quoteDraft.quote} />
                <FollowUpComposer
                  value={quoteBody}
                  placeholder="Ask about this text…"
                  disabled={false}
                  autoFocus
                  onChange={setQuoteBody}
                  onSend={() => void sendFollowup(quoteBody, quoteDraft.quote)}
                />
                <div className="flex justify-end">
                  <Button size="xs" variant="ghost" onClick={() => setQuoteDraft(null)}>
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              <Button
                size="sm"
                variant="secondary"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => setQuoteDraft((current) => current && { ...current, editing: true })}
              >
                Comment
              </Button>
            )}
          </div>,
          document.body,
        )}
    </section>
  );
}
