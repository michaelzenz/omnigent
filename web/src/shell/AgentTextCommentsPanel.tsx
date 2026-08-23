import { useEffect, useRef, useState } from "react";
import { Loader2Icon, PencilIcon, SendIcon, Trash2Icon } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { useSessionAgent } from "@/hooks/useAgents";
import {
  useAddAgentTextComment,
  useAgentTextComments,
  useDeleteAgentTextComment,
  useUpdateAgentTextComment,
} from "@/hooks/useAgentTextComments";
import { formatAgentTextComments } from "@/lib/formatAgentTextComments";
import { useChatStore } from "@/store/chatStore";
import { authenticatedFetch } from "@/lib/identity";
import { cn } from "@/lib/utils";
import { useAgentTextCommentsUI, type AgentTextCommentsUI } from "./AgentTextCommentsContext";

export function AgentTextCommentsPanel({
  conversationId,
  canEdit,
  ui: providedUI,
}: {
  conversationId: string;
  canEdit: boolean;
  ui?: AgentTextCommentsUI;
}) {
  const contextUI = useAgentTextCommentsUI();
  const ui = providedUI ?? contextUI;
  const queryClient = useQueryClient();
  const query = useAgentTextComments(conversationId);
  const add = useAddAgentTextComment(conversationId);
  const update = useUpdateAgentTextComment(conversationId);
  const remove = useDeleteAgentTextComment(conversationId);
  const { data: agent } = useSessionAgent(conversationId);
  const comments = query.data ?? [];
  const [draftBody, setDraftBody] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editBody, setEditBody] = useState("");
  const [sendError, setSendError] = useState<string | null>(null);
  const [clearingBatch, setClearingBatch] = useState(false);
  const { isSending, sentBatchIds } = ui.sendState;
  const selectedCardRef = useRef<HTMLDivElement>(null);
  const draftRef = useRef<HTMLTextAreaElement>(null);
  const conversationIdRef = useRef(conversationId);
  conversationIdRef.current = conversationId;

  useEffect(() => {
    if (!ui?.pendingAnchor) return;
    setDraftBody("");
    requestAnimationFrame(() => draftRef.current?.focus());
  }, [ui?.pendingAnchor]);

  useEffect(() => {
    if (!ui?.activeCommentId) return;
    requestAnimationFrame(() =>
      selectedCardRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }),
    );
  }, [ui?.activeCommentId]);

  const saveDraft = async () => {
    if (!ui?.pendingAnchor || !draftBody.trim()) return;
    const anchorConvId = conversationId;
    setSendError(null);
    try {
      const comment = await add.mutateAsync({ ...ui.pendingAnchor, body: draftBody.trim() });
      setDraftBody("");
      ui.cancelDraft();
      // Only activate if still on the same session — the POST may resolve
      // after the user has already switched away.
      if (conversationIdRef.current === anchorConvId) {
        ui.activateComment(comment.id);
      }
    } catch (error) {
      setSendError(error instanceof Error ? error.message : "Could not add comment.");
    }
  };

  const clearSentBatch = async (ids: string[], batchConversationId: string) => {
    setClearingBatch(true);
    try {
      const res = await authenticatedFetch(
        `/v1/sessions/${encodeURIComponent(batchConversationId)}/agent-text-comments/delete-batch`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ comment_ids: ids }),
        },
      );
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      // Invalidate the query for the session the comments belonged to,
      // not the currently-visible one.
      queryClient.invalidateQueries({
        queryKey: ["agent-text-comments", batchConversationId],
      });
      ui.setSendState({ isSending: false, sentBatchIds: null });
      setSendError(null);
      ui?.activateComment(null);
    } finally {
      setClearingBatch(false);
    }
  };

  const sendAll = async () => {
    if (
      !agent ||
      comments.length === 0 ||
      sentBatchIds ||
      isSending ||
      editingId ||
      ui?.pendingAnchor
    )
      return;
    // Capture the session at click time — the user may switch sessions
    // while the send POST is in-flight, and the batch delete must target
    // the session whose comments we're clearing, not the now-active one.
    const batchConversationId = conversationId;
    const batch = comments.map((comment) => ({ ...comment }));
    const ids = batch.map((comment) => comment.id);
    setSendError(null);
    ui.setSendState({ isSending: true, sentBatchIds: null });
    let policyDenied = false;
    try {
      await useChatStore.getState().send(formatAgentTextComments(batch), agent.id, [], {
        onPolicyDenied: () => {
          policyDenied = true;
        },
      });
    } catch (error) {
      setSendError(error instanceof Error ? error.message : "Could not send comments.");
      ui.setSendState({ isSending: false, sentBatchIds: null });
      return;
    }
    if (policyDenied) {
      setSendError("The message was denied by policy. Comments were not cleared.");
      ui.setSendState({ isSending: false, sentBatchIds: null });
      return;
    }

    ui.setSendState({ isSending: false, sentBatchIds: ids });
    try {
      await clearSentBatch(ids, batchConversationId);
    } catch {
      setSendError("Comments were sent, but could not be cleared. Retry clearing them.");
    }
  };

  const busy = isSending || add.isPending || clearingBatch || sentBatchIds !== null;

  const handleDelete = (commentId: string) => {
    setSendError(null);
    if (ui?.activeCommentId === commentId) {
      ui.activateComment(null);
    }
    remove.mutate(commentId, {
      onError: (error) => setSendError(error.message),
    });
  };

  const saveEdit = async (commentId: string) => {
    if (!editBody.trim() || update.isPending) return;
    setSendError(null);
    try {
      await update.mutateAsync({ id: commentId, body: editBody.trim() });
      setEditingId(null);
    } catch (error) {
      setSendError(
        error instanceof Error ? error.message : "Could not update comment.",
      );
    }
  };

  return (
    <section className="flex min-h-0 flex-1 flex-col" aria-label="Agent response comments">
      <header className="flex h-11 shrink-0 items-center justify-between border-b border-border px-3">
        <span className="text-sm font-semibold">Comments</span>
        {comments.length > 0 && (
          <span className="rounded-full bg-muted px-2 py-0.5 text-xs tabular-nums">
            {comments.length}
          </span>
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {ui?.pendingAnchor && (
          <div className="space-y-2 border-b border-border p-3">
            <Quote text={ui.pendingAnchor.selected_text} />
            <textarea
              ref={draftRef}
              rows={3}
              value={draftBody}
              disabled={!canEdit || busy}
              placeholder="Add a comment…"
              className="w-full resize-none rounded-md border border-border bg-background px-2 py-1.5 text-sm outline-none focus:border-ring"
              onChange={(event) => setDraftBody(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Escape" && !draftBody) ui.cancelDraft();
                if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                  event.preventDefault();
                  void saveDraft();
                }
              }}
            />
            <div className="flex justify-end gap-2">
              <Button type="button" size="xs" variant="ghost" onClick={ui.cancelDraft}>
                Cancel
              </Button>
              <Button
                type="button"
                size="xs"
                disabled={!canEdit || !draftBody.trim() || add.isPending}
                onClick={() => void saveDraft()}
              >
                {add.isPending && <Loader2Icon className="size-3 animate-spin" />}
                Add
              </Button>
            </div>
          </div>
        )}

        {query.isError ? (
          <div className="flex flex-col items-center gap-2 p-8 text-center text-sm text-destructive">
            Could not load comments.
            <Button
              type="button"
              size="xs"
              variant="ghost"
              onClick={() => query.refetch()}
            >
              Retry
            </Button>
          </div>
        ) : query.isLoading ? (
          <div className="flex justify-center p-8 text-sm text-muted-foreground">Loading…</div>
        ) : comments.length === 0 && !ui?.pendingAnchor ? (
          <div className="flex justify-center p-8 text-center text-sm text-muted-foreground">
            Select completed agent text and choose Comment.
          </div>
        ) : (
          <div className="space-y-2 p-3">
            {comments.map((comment) => {
              const selected = ui?.activeCommentId === comment.id;
              const editing = editingId === comment.id;
              return (
                <div
                  key={comment.id}
                  ref={selected ? selectedCardRef : undefined}
                  role="button"
                  tabIndex={0}
                  className={cn(
                    "cursor-pointer space-y-2 rounded-lg border p-3 text-sm",
                    selected ? "border-primary bg-primary/5" : "border-border bg-card",
                  )}
                  onClick={() => ui?.activateComment(comment.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") ui?.activateComment(comment.id);
                  }}
                >
                  <Quote text={comment.selected_text} />
                  {editing ? (
                    <div className="space-y-2" onClick={(event) => event.stopPropagation()}>
                      <textarea
                        autoFocus
                        rows={3}
                        value={editBody}
                        className="w-full resize-none rounded-md border border-border bg-background px-2 py-1.5"
                        onChange={(event) => setEditBody(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                            event.preventDefault();
                            void saveEdit(comment.id);
                          }
                          if (event.key === "Escape") setEditingId(null);
                        }}
                      />
                      <div className="flex justify-end gap-2">
                        <Button size="xs" variant="ghost" onClick={() => setEditingId(null)}>
                          Cancel
                        </Button>
                        <Button
                          size="xs"
                          disabled={!editBody.trim() || update.isPending}
                          onClick={() => void saveEdit(comment.id)}
                        >
                          Save
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <p className="whitespace-pre-wrap text-foreground">{comment.body}</p>
                  )}
                  {canEdit && !editing && (
                    <div
                      className="flex justify-end gap-1"
                      onClick={(event) => event.stopPropagation()}
                    >
                      <Button
                        type="button"
                        size="icon-xs"
                        variant="ghost"
                        aria-label="Edit comment"
                        disabled={busy}
                        onClick={() => {
                          setEditingId(comment.id);
                          setEditBody(comment.body);
                        }}
                      >
                        <PencilIcon className="size-3.5" />
                      </Button>
                      <Button
                        type="button"
                        size="icon-xs"
                        variant="ghost"
                        aria-label="Delete comment"
                        disabled={busy || remove.isPending}
                        onClick={() => handleDelete(comment.id)}
                      >
                        <Trash2Icon className="size-3.5" />
                      </Button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <footer className="shrink-0 space-y-2 border-t border-border p-3">
        {sendError && <p className="text-xs text-destructive">{sendError}</p>}
        <div className="flex justify-end">
          {sentBatchIds ? (
            <Button
              type="button"
              size="sm"
              disabled={clearingBatch}
              onClick={() => void clearSentBatch(sentBatchIds, conversationId)}
            >
              {clearingBatch && <Loader2Icon className="size-4 animate-spin" />}
              Retry clear
            </Button>
          ) : (
            <Button
              type="button"
              size="sm"
              disabled={
                !canEdit ||
                comments.length === 0 ||
                !agent ||
                busy ||
                editingId !== null ||
                ui?.pendingAnchor != null
              }
              onClick={() => void sendAll()}
            >
              {busy ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <SendIcon className="size-4" />
              )}
              {comments.length > 0 ? `Send ${comments.length} comments` : "Send comments"}
            </Button>
          )}
        </div>
      </footer>
    </section>
  );
}

function Quote({ text }: { text: string }) {
  return (
    <blockquote className="line-clamp-3 border-l-2 border-yellow-400/70 pl-2 text-xs text-muted-foreground">
      {text}
    </blockquote>
  );
}
