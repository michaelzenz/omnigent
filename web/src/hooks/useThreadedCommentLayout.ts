import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { useIsMobileViewport } from "./useIsMobileViewport";
import {
  AGENT_TEXT_THREAD_LAYOUT_EVENT,
  AGENT_TEXT_THREAD_LAYOUT_REQUEST_EVENT,
  type AgentTextThreadLayoutAnchor,
} from "./useAgentTextThreadHighlights";
import type { AgentTextThread } from "./useAgentTextThreads";

interface LayoutEventDetail {
  anchors: AgentTextThreadLayoutAnchor[];
  chatScrollHeight: number;
  chatClientHeight: number;
  chatScrollTop: number;
}

interface ThreadPosition {
  top: number;
  height: number;
}

function interpolate(value: number, from: number[], to: number[]): number {
  if (from.length === 0) return value;
  if (from.length === 1) return to[0] + (value - from[0]);
  if (value <= from[0]) return to[0] + (value - from[0]);
  for (let index = 1; index < from.length; index += 1) {
    if (value <= from[index]) {
      const span = from[index] - from[index - 1];
      const progress = span > 0 ? (value - from[index - 1]) / span : 0;
      return to[index - 1] + progress * (to[index] - to[index - 1]);
    }
  }
  const last = from.length - 1;
  return to[last] + (value - from[last]);
}

export function useThreadedCommentLayout(
  threads: AgentTextThread[],
  scrollerRef: RefObject<HTMLDivElement | null>,
): {
  positions: Map<string, ThreadPosition>;
  canvasHeight: number;
  onScroll: () => void;
} {
  const isMobile = useIsMobileViewport();
  const [detail, setDetail] = useState<LayoutEventDetail | null>(null);
  const [heights, setHeights] = useState<Map<string, number>>(new Map());
  const syncingFromChatRef = useRef(false);
  const releaseTimerRef = useRef<number | null>(null);

  useEffect(() => {
    if (isMobile) return;
    const handleLayout = (event: Event) => {
      const next = (event as CustomEvent<LayoutEventDetail>).detail;
      setDetail(next);
    };
    window.addEventListener(AGENT_TEXT_THREAD_LAYOUT_EVENT, handleLayout);
    window.dispatchEvent(new Event(AGENT_TEXT_THREAD_LAYOUT_REQUEST_EVENT));
    return () => window.removeEventListener(AGENT_TEXT_THREAD_LAYOUT_EVENT, handleLayout);
  }, [isMobile]);

  useEffect(() => {
    if (isMobile) return;
    const scroller = scrollerRef.current;
    if (!scroller) return;
    const measure = () => {
      const next = new Map<string, number>();
      for (const thread of threads) {
        const card = scroller.querySelector<HTMLElement>(
          `[data-agent-thread-card="${CSS.escape(thread.id)}"]`,
        );
        if (card) next.set(thread.id, card.offsetHeight);
      }
      setHeights(next);
    };
    const frame = requestAnimationFrame(measure);
    const observer = new ResizeObserver(measure);
    for (const card of scroller.querySelectorAll<HTMLElement>("[data-agent-thread-card]")) {
      observer.observe(card);
    }
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [isMobile, scrollerRef, threads]);

  const positions = useMemo(() => {
    const result = new Map<string, ThreadPosition>();
    if (isMobile || !detail) return result;
    const anchors = new Map(detail.anchors.map((anchor) => [anchor.threadId, anchor.anchorY]));
    let previousBottom = 0;
    for (const thread of threads) {
      const anchorY = anchors.get(thread.id);
      if (anchorY == null) continue;
      const height = heights.get(thread.id) ?? 140;
      const top = Math.max(anchorY, previousBottom === 0 ? anchorY : previousBottom + 12);
      result.set(thread.id, { top, height });
      previousBottom = top + height;
    }
    return result;
  }, [detail, heights, isMobile, threads]);

  const canvasHeight = useMemo(() => {
    if (isMobile) return 0;
    let lastBottom = detail?.chatScrollHeight ?? 0;
    for (const position of positions.values()) {
      lastBottom = Math.max(lastBottom, position.top + position.height + 24);
    }
    return lastBottom;
  }, [detail?.chatScrollHeight, isMobile, positions]);

  useEffect(() => {
    if (isMobile || !detail || positions.size === 0) return;
    const scroller = scrollerRef.current;
    if (!scroller) return;
    const points = threads.flatMap((thread) => {
      const anchor = detail.anchors.find((candidate) => candidate.threadId === thread.id);
      const position = positions.get(thread.id);
      return anchor && position ? [{ anchor: anchor.anchorY, card: position.top }] : [];
    });
    if (points.length === 0) return;
    const chatReference = detail.chatScrollTop + detail.chatClientHeight * 0.35;
    const commentReference = interpolate(
      chatReference,
      points.map((point) => point.anchor),
      points.map((point) => point.card),
    );
    syncingFromChatRef.current = true;
    scroller.scrollTop = Math.max(0, commentReference - scroller.clientHeight * 0.35);
    if (releaseTimerRef.current !== null) window.clearTimeout(releaseTimerRef.current);
    releaseTimerRef.current = window.setTimeout(() => {
      syncingFromChatRef.current = false;
      releaseTimerRef.current = null;
    }, 100);
  }, [detail, isMobile, positions, scrollerRef, threads]);

  useEffect(
    () => () => {
      if (releaseTimerRef.current !== null) window.clearTimeout(releaseTimerRef.current);
    },
    [],
  );

  const onScroll = useCallback(() => {
    if (isMobile || syncingFromChatRef.current || !detail || positions.size === 0) return;
    const commentsScroller = scrollerRef.current;
    const chatScroller = document.querySelector<HTMLElement>(".transcript-hide-native-scrollbar");
    if (!commentsScroller || !chatScroller) return;
    const points = threads.flatMap((thread) => {
      const anchor = detail.anchors.find((candidate) => candidate.threadId === thread.id);
      const position = positions.get(thread.id);
      return anchor && position ? [{ anchor: anchor.anchorY, card: position.top }] : [];
    });
    if (points.length === 0) return;
    const commentReference = commentsScroller.scrollTop + commentsScroller.clientHeight * 0.35;
    const chatReference = interpolate(
      commentReference,
      points.map((point) => point.card),
      points.map((point) => point.anchor),
    );
    chatScroller.scrollTop = Math.min(
      Math.max(0, chatReference - chatScroller.clientHeight * 0.35),
      chatScroller.scrollHeight - chatScroller.clientHeight,
    );
  }, [detail, isMobile, positions, scrollerRef, threads]);

  return { positions, canvasHeight, onScroll };
}
