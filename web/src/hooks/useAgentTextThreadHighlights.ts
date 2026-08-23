import { useEffect, useRef, type RefObject } from "react";
import type { AgentTextCommentAnchor } from "./useAgentTextComments";
import type { AgentTextThread } from "./useAgentTextThreads";
import { resolveAgentTextCommentRange } from "@/lib/agentTextSelection";
import { useChatStore } from "@/store/chatStore";

interface HighlightRegistry {
  set: (name: string, highlight: unknown) => void;
  delete: (name: string) => void;
}

interface HighlightConstructor {
  new (...ranges: Range[]): unknown;
}

export interface AgentTextThreadLayoutAnchor {
  threadId: string;
  anchorY: number;
}

export const AGENT_TEXT_THREAD_LAYOUT_EVENT = "omnigent:agent-text-thread-layout";
export const AGENT_TEXT_THREAD_LAYOUT_REQUEST_EVENT = "omnigent:agent-text-thread-layout-request";

const BASE = "omnigent-agent-thread";
const ACTIVE = "omnigent-agent-thread-active";
const PENDING = "omnigent-agent-thread-pending";

function registry(): { highlights: HighlightRegistry; Highlight: HighlightConstructor } | null {
  const css = CSS as typeof CSS & { highlights?: HighlightRegistry };
  const ctor = (window as typeof window & { Highlight?: HighlightConstructor }).Highlight;
  return css.highlights && ctor ? { highlights: css.highlights, Highlight: ctor } : null;
}

function rootFor(container: HTMLElement, itemId: string): HTMLElement | null {
  return container.querySelector<HTMLElement>(`[data-agent-text-item-id="${CSS.escape(itemId)}"]`);
}

function threadAnchor(thread: AgentTextThread): AgentTextCommentAnchor {
  return {
    conversation_item_id: thread.source_item_id,
    start_offset: thread.start_offset,
    end_offset: thread.end_offset,
    selected_text: thread.selected_text,
    prefix_context: thread.prefix_context,
    suffix_context: thread.suffix_context,
  };
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

export function useAgentTextThreadHighlights({
  containerRef,
  threads,
  navigationThreads = threads,
  pendingAnchor,
  activeThreadId,
  onActivate,
  enabled,
}: {
  containerRef: RefObject<HTMLElement | null>;
  threads: AgentTextThread[];
  navigationThreads?: AgentTextThread[];
  pendingAnchor: AgentTextCommentAnchor | null;
  activeThreadId: string | null;
  onActivate: (threadId: string | null) => void;
  enabled: boolean;
}): void {
  const rangesRef = useRef<{ thread: AgentTextThread; range: Range }[]>([]);

  useEffect(() => {
    const container = containerRef.current;
    const api = registry();
    if (!enabled || !container || !api) return;
    let frame = 0;

    const rebuild = () => {
      frame = 0;
      const base: Range[] = [];
      const active: Range[] = [];
      const resolved: { thread: AgentTextThread; range: Range }[] = [];
      const layout: AgentTextThreadLayoutAnchor[] = [];
      const scrollElement = container.querySelector<HTMLElement>(
        ".transcript-hide-native-scrollbar",
      );
      const scrollRect = scrollElement?.getBoundingClientRect();

      for (const thread of threads) {
        const root = rootFor(container, thread.source_item_id);
        if (!root) continue;
        const range = resolveAgentTextCommentRange(root, threadAnchor(thread));
        if (!range) continue;
        resolved.push({ thread, range });
        if (thread.id === activeThreadId) active.push(range);
        else base.push(range);
        const rect = Array.from(range.getClientRects()).find(
          (candidate) => candidate.width > 0 && candidate.height > 0,
        );
        if (rect && scrollElement && scrollRect) {
          layout.push({
            threadId: thread.id,
            anchorY: rect.top - scrollRect.top + scrollElement.scrollTop,
          });
        }
      }

      const pending: Range[] = [];
      let pendingAnchorY: number | null = null;
      if (pendingAnchor) {
        const root = rootFor(container, pendingAnchor.conversation_item_id);
        const range = root ? resolveAgentTextCommentRange(root, pendingAnchor) : null;
        if (range) {
          pending.push(range);
          const rect = Array.from(range.getClientRects()).find(
            (candidate) => candidate.width > 0 && candidate.height > 0,
          );
          if (rect && scrollElement && scrollRect) {
            pendingAnchorY = rect.top - scrollRect.top + scrollElement.scrollTop;
          }
        }
      }

      rangesRef.current = resolved;
      api.highlights.set(BASE, new api.Highlight(...base));
      api.highlights.set(ACTIVE, new api.Highlight(...active));
      api.highlights.set(PENDING, new api.Highlight(...pending));
      window.dispatchEvent(
        new CustomEvent(AGENT_TEXT_THREAD_LAYOUT_EVENT, {
          detail: {
            anchors: layout,
            pendingAnchorY,
            chatScrollHeight: scrollElement?.scrollHeight ?? 0,
            chatClientHeight: scrollElement?.clientHeight ?? 0,
            chatScrollTop: scrollElement?.scrollTop ?? 0,
          },
        }),
      );
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(rebuild);
    };
    rebuild();
    const observer = new MutationObserver(schedule);
    observer.observe(container, { childList: true, subtree: true, characterData: true });
    const scrollElement = container.querySelector<HTMLElement>(".transcript-hide-native-scrollbar");
    scrollElement?.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    window.addEventListener(AGENT_TEXT_THREAD_LAYOUT_REQUEST_EVENT, schedule);
    return () => {
      observer.disconnect();
      scrollElement?.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      window.removeEventListener(AGENT_TEXT_THREAD_LAYOUT_REQUEST_EVENT, schedule);
      if (frame) cancelAnimationFrame(frame);
      rangesRef.current = [];
      api.highlights.delete(BASE);
      api.highlights.delete(ACTIVE);
      api.highlights.delete(PENDING);
    };
  }, [activeThreadId, containerRef, enabled, pendingAnchor, threads]);

  useEffect(() => {
    if (!enabled || !activeThreadId) return;
    const container = containerRef.current;
    const thread = navigationThreads.find((candidate) => candidate.id === activeThreadId);
    if (!container || !thread) return;
    let cancelled = false;

    const scrollToThread = async () => {
      let root = rootFor(container, thread.source_item_id);
      /* oxlint-disable no-await-in-loop -- history pages must load in cursor order. */
      for (let page = 0; !root && page < 1000; page += 1) {
        const state = useChatStore.getState();
        if (!state.hasMoreHistory || cancelled) return;
        await state.loadMoreHistory();
        await new Promise<void>((resolve) => {
          requestAnimationFrame(() => resolve());
        });
        root = rootFor(container, thread.source_item_id);
      }
      /* oxlint-enable no-await-in-loop */
      if (!root || cancelled) return;
      const range = resolveAgentTextCommentRange(root, threadAnchor(thread));
      range?.startContainer.parentElement?.scrollIntoView({ behavior: "smooth", block: "center" });
    };

    void scrollToThread();
    return () => {
      cancelled = true;
    };
  }, [activeThreadId, containerRef, enabled, navigationThreads]);

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
      matches.sort((a, b) => a.thread.selected_text.length - b.thread.selected_text.length);
      onActivate(matches[0]?.thread.id ?? null);
    };
    container.addEventListener("click", handleClick);
    return () => container.removeEventListener("click", handleClick);
  }, [containerRef, enabled, onActivate]);
}
