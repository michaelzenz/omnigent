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
  pendingAnchorY: number | null;
  chatScrollHeight: number;
  chatClientHeight: number;
  chatScrollTop: number;
}

interface ThreadPosition {
  top: number;
  height: number;
}

const DRAFT_KEY = "__draft__";

function sharedAlignmentY(chatScroller: HTMLElement, commentsScroller: HTMLElement): number {
  const chatRect = chatScroller.getBoundingClientRect();
  const commentsRect = commentsScroller.getBoundingClientRect();
  const sharedTop = Math.max(chatRect.top, commentsRect.top);
  const sharedBottom = Math.min(chatRect.bottom, commentsRect.bottom);
  return Math.min(sharedBottom - 1, sharedTop + 24);
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
  hasPendingDraft: boolean,
  suppressAutoSync: boolean,
): {
  positions: Map<string, ThreadPosition>;
  draftTop: number | null;
  canvasHeight: number;
  onScroll: () => void;
} {
  const isMobile = useIsMobileViewport();
  const [detail, setDetail] = useState<LayoutEventDetail | null>(null);
  const [heights, setHeights] = useState<Map<string, number>>(new Map());
  const syncingFromChatRef = useRef(false);
  const releaseTimerRef = useRef<number | null>(null);
  const lastChatScrollTopRef = useRef<number | null>(null);
  const hadPendingDraftRef = useRef(false);

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
      const draft = scroller.querySelector<HTMLElement>("[data-agent-thread-draft]");
      if (draft) next.set(DRAFT_KEY, draft.offsetHeight);
      setHeights(next);
    };
    const frame = requestAnimationFrame(measure);
    const observer = new ResizeObserver(measure);
    for (const card of scroller.querySelectorAll<HTMLElement>(
      "[data-agent-thread-card], [data-agent-thread-draft]",
    )) {
      observer.observe(card);
    }
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [hasPendingDraft, isMobile, scrollerRef, threads]);

  const layout = useMemo(() => {
    const positions = new Map<string, ThreadPosition>();
    if (isMobile || !detail) return { positions, draftTop: null as number | null };
    const anchors = new Map(detail.anchors.map((anchor) => [anchor.threadId, anchor.anchorY]));
    const items = threads.flatMap((thread) => {
      const anchorY = anchors.get(thread.id);
      return anchorY == null
        ? []
        : [{ key: thread.id, anchorY, height: heights.get(thread.id) ?? 140 }];
    });
    if (hasPendingDraft && detail.pendingAnchorY !== null) {
      items.push({
        key: DRAFT_KEY,
        anchorY: detail.pendingAnchorY,
        height: heights.get(DRAFT_KEY) ?? 190,
      });
    }
    items.sort((left, right) => left.anchorY - right.anchorY);

    let previousBottom = 0;
    let draftTop: number | null = null;
    for (const item of items) {
      const top = Math.max(item.anchorY, previousBottom === 0 ? item.anchorY : previousBottom + 12);
      if (item.key === DRAFT_KEY) draftTop = top;
      else positions.set(item.key, { top, height: item.height });
      previousBottom = top + item.height;
    }
    return { positions, draftTop };
  }, [detail, hasPendingDraft, heights, isMobile, threads]);
  const { positions, draftTop } = layout;

  const canvasHeight = useMemo(() => {
    if (isMobile) return 0;
    let lastBottom = detail?.chatScrollHeight ?? 0;
    for (const position of positions.values()) {
      lastBottom = Math.max(lastBottom, position.top + position.height + 24);
    }
    if (draftTop !== null) {
      lastBottom = Math.max(lastBottom, draftTop + (heights.get(DRAFT_KEY) ?? 190) + 24);
    }
    return lastBottom;
  }, [detail?.chatScrollHeight, draftTop, heights, isMobile, positions]);

  const mappingPoints = useMemo(() => {
    if (!detail) return [];
    const points = threads.flatMap((thread) => {
      const anchor = detail.anchors.find((candidate) => candidate.threadId === thread.id);
      const position = positions.get(thread.id);
      return anchor && position ? [{ anchor: anchor.anchorY, card: position.top }] : [];
    });
    if (draftTop !== null && detail.pendingAnchorY !== null) {
      points.push({ anchor: detail.pendingAnchorY, card: draftTop });
    }
    return points.sort((left, right) => left.anchor - right.anchor);
  }, [detail, draftTop, positions, threads]);

  useEffect(() => {
    if (isMobile || !detail || mappingPoints.length === 0) return;
    const scroller = scrollerRef.current;
    const chatScroller = document.querySelector<HTMLElement>(".transcript-hide-native-scrollbar");
    if (!scroller || !chatScroller) return;
    const previousChatScrollTop = lastChatScrollTopRef.current;
    const chatMoved =
      previousChatScrollTop === null || Math.abs(detail.chatScrollTop - previousChatScrollTop) >= 1;
    const draftOpened = hasPendingDraft && !hadPendingDraftRef.current;
    lastChatScrollTopRef.current = detail.chatScrollTop;
    hadPendingDraftRef.current = hasPendingDraft;
    if (suppressAutoSync) return;
    // Streamed response chunks change card heights and dispatch fresh layout
    // events. They must not move either pane unless the user actually scrolled
    // the chat or opened a new draft that needs initial alignment.
    if (!chatMoved && !draftOpened) return;
    const screenY = sharedAlignmentY(chatScroller, scroller);
    const chatReference =
      chatScroller.scrollTop + screenY - chatScroller.getBoundingClientRect().top;
    const commentReference = interpolate(
      chatReference,
      mappingPoints.map((point) => point.anchor),
      mappingPoints.map((point) => point.card),
    );
    syncingFromChatRef.current = true;
    scroller.scrollTop = Math.min(
      Math.max(0, commentReference - (screenY - scroller.getBoundingClientRect().top)),
      Math.max(0, scroller.scrollHeight - scroller.clientHeight),
    );
    if (releaseTimerRef.current !== null) window.clearTimeout(releaseTimerRef.current);
    releaseTimerRef.current = window.setTimeout(() => {
      syncingFromChatRef.current = false;
      releaseTimerRef.current = null;
    }, 100);
  }, [detail, hasPendingDraft, isMobile, mappingPoints, scrollerRef, suppressAutoSync]);

  useEffect(
    () => () => {
      if (releaseTimerRef.current !== null) window.clearTimeout(releaseTimerRef.current);
    },
    [],
  );

  const onScroll = useCallback(() => {
    if (isMobile || syncingFromChatRef.current || mappingPoints.length === 0) return;
    const commentsScroller = scrollerRef.current;
    const chatScroller = document.querySelector<HTMLElement>(".transcript-hide-native-scrollbar");
    if (!commentsScroller || !chatScroller) return;
    const screenY = sharedAlignmentY(chatScroller, commentsScroller);
    const commentReference =
      commentsScroller.scrollTop + screenY - commentsScroller.getBoundingClientRect().top;
    const chatReference = interpolate(
      commentReference,
      mappingPoints.map((point) => point.card),
      mappingPoints.map((point) => point.anchor),
    );
    chatScroller.scrollTop = Math.min(
      Math.max(0, chatReference - (screenY - chatScroller.getBoundingClientRect().top)),
      Math.max(0, chatScroller.scrollHeight - chatScroller.clientHeight),
    );
  }, [isMobile, mappingPoints, scrollerRef]);

  return { positions, draftTop, canvasHeight, onScroll };
}
