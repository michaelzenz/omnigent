import { type RefObject, useCallback, useEffect, useLayoutEffect, useRef } from "react";

/** The card a page read keeps in place: the first for a page above, the last for a page below. */
export type PageReadEdge = "first" | "last";

/**
 * Keeps the cards on screen in place when a page loads at one end of the list
 * and the page at the other end drops. The browser's scroll anchoring does this
 * only when it anchors on a card; the list scrolls by any shift that is left.
 *
 * @param scrollRoot - The element the cards scroll in.
 * @param cards - The element that holds the cards.
 * @param reading - Whether a page read is in flight.
 * @returns Marks the card to keep in place; call it when a page read starts.
 */
export function usePageReadAnchor(
  scrollRoot: RefObject<HTMLElement | null>,
  cards: RefObject<HTMLElement | null>,
  reading: boolean,
): (edge: PageReadEdge) => void {
  const anchor = useRef<{ card: Element; top: number } | null>(null);
  const wasReading = useRef(false);
  useEffect(() => {
    const root = scrollRoot.current;
    if (root === null) return;
    const measureAnchor = () => {
      if (anchor.current) anchor.current.top = anchor.current.card.getBoundingClientRect().top;
    };
    root.addEventListener("scroll", measureAnchor, { passive: true });
    return () => root.removeEventListener("scroll", measureAnchor);
  }, [scrollRoot]);
  useLayoutEffect(() => {
    const finished = wasReading.current && !reading;
    wasReading.current = reading;
    const marked = anchor.current;
    const root = scrollRoot.current;
    if (!finished || marked === null || root === null) return;
    anchor.current = null;
    if (!marked.card.isConnected) return;
    const shift = marked.card.getBoundingClientRect().top - marked.top;
    if (shift !== 0) root.scrollTop += shift;
  });
  return useCallback(
    (edge: PageReadEdge) => {
      const list = cards.current;
      const card = edge === "first" ? list?.firstElementChild : list?.lastElementChild;
      anchor.current = card ? { card, top: card.getBoundingClientRect().top } : null;
    },
    [cards],
  );
}
