import { useEffect, useRef, type RefObject } from "react";
import type { AgentTextComment, AgentTextCommentAnchor } from "./useAgentTextComments";
import { resolveAgentTextCommentRange } from "@/lib/agentTextSelection";
import { useChatStore } from "@/store/chatStore";

interface HighlightRegistry {
  set: (name: string, highlight: unknown) => void;
  delete: (name: string) => void;
}

interface HighlightConstructor {
  new (...ranges: Range[]): unknown;
}

const BASE = "omnigent-agent-comment";
const ACTIVE = "omnigent-agent-comment-active";
const PENDING = "omnigent-agent-comment-pending";

function registry(): { highlights: HighlightRegistry; Highlight: HighlightConstructor } | null {
  const css = CSS as typeof CSS & { highlights?: HighlightRegistry };
  const ctor = (window as typeof window & { Highlight?: HighlightConstructor }).Highlight;
  return css.highlights && ctor ? { highlights: css.highlights, Highlight: ctor } : null;
}

function rootFor(container: HTMLElement, itemId: string): HTMLElement | null {
  return container.querySelector<HTMLElement>(`[data-agent-text-item-id="${CSS.escape(itemId)}"]`);
}

function caretRangeAtPoint(x: number, y: number): Range | null {
  const doc = document as Document & {
    caretRangeFromPoint?: (x: number, y: number) => Range | null;
    caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null;
  };
  if (doc.caretRangeFromPoint) return doc.caretRangeFromPoint(x, y);
  const point = doc.caretPositionFromPoint?.(x, y);
  if (!point) return null;
  const range = document.createRange();
  range.setStart(point.offsetNode, point.offset);
  range.collapse(true);
  return range;
}

export function useAgentTextCommentHighlights({
  containerRef,
  comments,
  pendingAnchor,
  activeCommentId,
  onActivate,
  enabled = true,
}: {
  containerRef: RefObject<HTMLElement | null>;
  comments: AgentTextComment[];
  pendingAnchor: AgentTextCommentAnchor | null;
  activeCommentId: string | null;
  onActivate: (commentId: string | null) => void;
  enabled?: boolean;
}): void {
  const rangesRef = useRef<{ comment: AgentTextComment; range: Range }[]>([]);

  useEffect(() => {
    const container = containerRef.current;
    const api = registry();
    if (!enabled || !container || !api) return;
    let frame = 0;

    const rebuild = () => {
      frame = 0;
      const base: Range[] = [];
      const active: Range[] = [];
      const resolved: { comment: AgentTextComment; range: Range }[] = [];
      for (const comment of comments) {
        const root = rootFor(container, comment.conversation_item_id);
        if (!root || !container.contains(root)) continue;
        const range = resolveAgentTextCommentRange(root, comment);
        if (!range) continue;
        resolved.push({ comment, range });
        if (comment.id === activeCommentId) active.push(range);
        else base.push(range);
      }
      const pending: Range[] = [];
      if (pendingAnchor) {
        const root = rootFor(container, pendingAnchor.conversation_item_id);
        if (root && container.contains(root)) {
          const range = resolveAgentTextCommentRange(root, pendingAnchor);
          if (range) pending.push(range);
        }
      }
      rangesRef.current = resolved;
      api.highlights.set(BASE, new api.Highlight(...base));
      api.highlights.set(ACTIVE, new api.Highlight(...active));
      api.highlights.set(PENDING, new api.Highlight(...pending));
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(rebuild);
    };
    rebuild();
    const observer = new MutationObserver(schedule);
    observer.observe(container, { childList: true, subtree: true, characterData: true });
    return () => {
      observer.disconnect();
      if (frame) cancelAnimationFrame(frame);
      rangesRef.current = [];
      api.highlights.delete(BASE);
      api.highlights.delete(ACTIVE);
      api.highlights.delete(PENDING);
    };
  }, [activeCommentId, comments, containerRef, enabled, pendingAnchor]);

  useEffect(() => {
    if (!enabled || !activeCommentId) return;
    const container = containerRef.current;
    const comment = comments.find((candidate) => candidate.id === activeCommentId);
    if (!container || !comment) return;
    let cancelled = false;

    const nextFrame = () =>
      new Promise<void>((resolve) => {
        requestAnimationFrame(() => resolve());
      });
    const scrollToComment = async () => {
      let root = rootFor(container, comment.conversation_item_id);
      /* oxlint-disable no-await-in-loop -- history pages must load in cursor order. */
      for (let page = 0; !root && page < 1000; page += 1) {
        const state = useChatStore.getState();
        if (!state.hasMoreHistory || cancelled) return;
        await state.loadMoreHistory();
        await nextFrame();
        root = rootFor(container, comment.conversation_item_id);
      }
      /* oxlint-enable no-await-in-loop */
      if (!root || cancelled) return;
      const range = resolveAgentTextCommentRange(root, comment);
      if (!range) return;
      const rect = range.getBoundingClientRect();
      if (rect.top >= 80 && rect.bottom <= window.innerHeight - 40) return;
      range.startContainer.parentElement?.scrollIntoView({ behavior: "smooth", block: "center" });
    };

    void scrollToComment();
    return () => {
      cancelled = true;
    };
  }, [activeCommentId, comments, containerRef, enabled]);

  useEffect(() => {
    const container = containerRef.current;
    if (!enabled || !container) return;
    const handleClick = (event: MouseEvent) => {
      if (window.getSelection()?.isCollapsed === false) return;
      const point = caretRangeAtPoint(event.clientX, event.clientY);
      if (!point) return;
      const matches = rangesRef.current.filter(({ range }) => {
        try {
          return range.isPointInRange(point.startContainer, point.startOffset);
        } catch {
          return false;
        }
      });
      matches.sort((a, b) => a.comment.selected_text.length - b.comment.selected_text.length);
      onActivate(matches[0]?.comment.id ?? null);
    };
    container.addEventListener("click", handleClick);
    return () => container.removeEventListener("click", handleClick);
  }, [containerRef, enabled, onActivate]);
}
