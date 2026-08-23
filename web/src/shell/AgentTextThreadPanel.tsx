import { useEffect, useMemo, useRef, useState } from "react";
import { CheckIcon, Loader2Icon, RotateCcwIcon, Trash2Icon } from "lucide-react";
import { FilePathAwareMessageResponse } from "@/components/blocks/ChatMarkdown";
import { Button } from "@/components/ui/button";
import { useIsMobileViewport } from "@/hooks/useIsMobileViewport";
import { useThreadedCommentLayout } from "@/hooks/useThreadedCommentLayout";
import {
  useAgentTextThreads,
  useCreateAgentTextThread,
  useDeleteAgentTextThread,
  useResolveAgentTextThread,
  useRetryAgentTextThread,
  type AgentTextThread,
  type AgentTextThreadView,
} from "@/hooks/useAgentTextThreads";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chatStore";
import type { AgentTextCommentsUI } from "./AgentTextCommentsContext";

const EMPTY_THREADS: AgentTextThread[] = [];

function assistantText(thread: AgentTextThread): string {
  const parts: string[] = [];
  for (const item of thread.items) {
    if (item.type !== "message" || item.role !== "assistant" || !Array.isArray(item.content)) continue;
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
  const [draftBody, setDraftBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  const draftRequestIdRef = useRef(crypto.randomUUID());
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
  const liveAnswers = useMemo(() => {
    const result = new Map<string, string>();
    for (const thread of threads) {
      if (!thread.response_id) continue;
      const responseBlocks = liveBlocks.filter(
        (block) => block.ctx.responseId === thread.response_id,
      );
      const completed = responseBlocks.flatMap((block) =>
        block.type === "text_done" ? [block.fullText] : [],
      );
      const text =
        completed.length > 0
          ? completed.join("\n")
          : responseBlocks
              .flatMap((block) => (block.type === "text_chunk" ? [block.text] : []))
              .join("");
      if (text) result.set(thread.id, text);
    }
    return result;
  }, [liveBlocks, threads]);
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const isMobile = useIsMobileViewport();
  const { positions, canvasHeight, onScroll } = useThreadedCommentLayout(threads, scrollerRef);

  useEffect(() => {
    draftRequestIdRef.current = crypto.randomUUID();
    setDraftBody("");
    setError(null);
  }, [ui.pendingAnchor]);

  const saveDraft = async () => {
    const anchor = ui.pendingAnchor;
    if (!anchor || !draftBody.trim()) return;
    const requestId = draftRequestIdRef.current;
    setError(null);
    try {
      const thread = await create.mutateAsync({
        ...anchor,
        client_request_id: requestId,
        comment: draftBody.trim(),
      });
      // A newer text selection may have opened another draft while this send
      // was pending. Never let the older completion clear or replace it.
      if (
        useChatStore.getState().conversationId === conversationId &&
        pendingAnchorRef.current === anchor &&
        draftRequestIdRef.current === requestId
      ) {
        setDraftBody("");
        ui.cancelDraft();
        ui.activateComment(thread.id);
        setView("open");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not create threaded comment.");
    }
  };

  const openCount = openQuery.data?.length ?? 0;
  const activeThread = useMemo(
    () => threads.find((thread) => thread.id === ui.activeCommentId) ?? null,
    [threads, ui.activeCommentId],
  );

  useEffect(() => {
    if (!activeThread) return;
    document
      .querySelector<HTMLElement>(`[data-agent-thread-card="${CSS.escape(activeThread.id)}"]`)
      ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [activeThread]);

  return (
    <section className="flex min-h-0 flex-1 flex-col" aria-label="Threaded agent comments">
      <div className="flex shrink-0 gap-1 border-b border-border px-3 py-2">
        <Button size="xs" variant={view === "open" ? "secondary" : "ghost"} onClick={() => setView("open")}>
          Open {openCount > 0 ? openCount : ""}
        </Button>
        <Button size="xs" variant={view === "resolved" ? "secondary" : "ghost"} onClick={() => setView("resolved")}>
          Resolved
        </Button>
      </div>
      <div
        ref={scrollerRef}
        className="min-h-0 flex-1 overflow-y-auto"
        data-agent-thread-scroller
        onScroll={onScroll}
      >
        {ui.pendingAnchor && view === "open" && (
          <div className="space-y-2 border-b border-border p-3">
            <Quote text={ui.pendingAnchor.selected_text} />
            <textarea
              autoFocus
              rows={4}
              value={draftBody}
              placeholder="Ask about this response…"
              className="w-full resize-none rounded-md border border-border bg-background px-2 py-1.5 text-sm outline-none focus:border-ring"
              onChange={(event) => setDraftBody(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                  event.preventDefault();
                  void saveDraft();
                }
              }}
            />
            <div className="flex justify-end gap-2">
              <Button size="xs" variant="ghost" onClick={ui.cancelDraft}>Cancel</Button>
              <Button size="xs" disabled={!canEdit || !draftBody.trim() || create.isPending} onClick={() => void saveDraft()}>
                {create.isPending && <Loader2Icon className="size-3 animate-spin" />}
                Send comment
              </Button>
            </div>
          </div>
        )}
        {query.isError ? (
          <div className="flex flex-col items-center gap-2 p-8 text-sm text-destructive">
            Could not load threaded replies.
            <Button size="xs" variant="ghost" onClick={() => query.refetch()}>Retry</Button>
          </div>
        ) : query.isLoading ? (
          <div className="flex justify-center p-8 text-sm text-muted-foreground">Loading…</div>
        ) : threads.length === 0 && !ui.pendingAnchor ? (
          <div className="p-8 text-center text-sm text-muted-foreground">
            {view === "open" ? "Select completed agent text and choose Comment." : "No resolved threads."}
          </div>
        ) : (
          <div
            className={cn("relative p-3", (isMobile || positions.size === 0) && "space-y-3")}
            style={!isMobile && canvasHeight > 0 ? { height: canvasHeight } : undefined}
            data-agent-thread-canvas
          >
            {threads.map((thread) => {
              const selected = ui.activeCommentId === thread.id;
              const answer = liveAnswers.get(thread.id) ?? assistantText(thread);
              const isStreaming =
                thread.response_id !== null &&
                activeResponse?.responseId === thread.response_id &&
                activeResponse.state === "streaming";
              const position = positions.get(thread.id);
              return (
                <article
                  key={thread.id}
                  data-agent-thread-card={thread.id}
                  style={
                    !isMobile && position
                      ? { position: "absolute", top: position.top, left: 12, right: 12 }
                      : undefined
                  }
                  className={cn(
                    "space-y-2 rounded-lg border bg-card p-3 text-sm",
                    selected && "border-purple-500 bg-purple-500/5",
                  )}
                  onClick={() => ui.activateComment(thread.id)}
                >
                  <Quote text={thread.selected_text} />
                  <p className="whitespace-pre-wrap">{thread.user_comment}</p>
                  <div className="border-t border-border pt-2">
                    {thread.state === "failed" ? (
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
                    ) : (
                      <div className={cn(!selected && "max-h-32 overflow-hidden")}>
                        <FilePathAwareMessageResponse>
                          {answer || "Agent answered."}
                        </FilePathAwareMessageResponse>
                      </div>
                    )}
                  </div>
                  {canEdit && view === "open" && (
                    <div className="flex justify-end gap-1">
                      {thread.state === "answered" && (
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
                </article>
              );
            })}
          </div>
        )}
      </div>
      {error && <p className="shrink-0 border-t border-border p-3 text-xs text-destructive">{error}</p>}
    </section>
  );
}
