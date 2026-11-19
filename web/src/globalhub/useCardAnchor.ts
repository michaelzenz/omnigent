import { type RefObject, useCallback, useEffect, useLayoutEffect, useRef } from "react";

/** A card, and where its top was, in pixels below the top of the scroll root. */
interface CardPlace {
  id: string;
  offset: number;
}

/** A card that the user moved down the board, and where the card after it goes. */
interface MovedCard {
  id: string;
  successor: CardPlace;
}

function cardElements(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>("[data-task-id]")];
}

function findCard(root: HTMLElement, id: string): HTMLElement | undefined {
  return cardElements(root).find((card) => card.dataset.taskId === id);
}

function offsetOf(root: HTMLElement, card: HTMLElement): number {
  return card.getBoundingClientRect().top - root.getBoundingClientRect().top;
}

/** Scrolls the root so that the card's top is at its place again; returns the card. */
function restore(root: HTMLElement, place: CardPlace): HTMLElement | undefined {
  const card = findCard(root, place.id);
  if (card) root.scrollTop += offsetOf(root, card) - place.offset;
  return card;
}

function movedAway(order: readonly string[], moved: MovedCard): boolean {
  const index = order.indexOf(moved.id);
  const successorIndex = order.indexOf(moved.successor.id);
  return index === -1 || (successorIndex !== -1 && successorIndex < index);
}

/**
 * Keeps the card that the user looks at in place on screen while the order of
 * the cards changes, e.g. when a new task comes in above it or a task moves to
 * the top. That card is the first one that reaches below the top of the scroll
 * root, and it stays in place also when it moves itself. A card that the user
 * moves down the board does not take the view along: the card after it takes
 * its place on screen, and gets the focus. A change of scope moves nothing.
 *
 * @param scrollRoot - The element the cards scroll in; each card has ``data-task-id``.
 * @param order - The ids of the shown cards, in order.
 * @param scope - What the cards show, e.g. the search and the priority filter.
 * @returns Marks a card that the user moves down the board; call the result if the move fails.
 */
export function useCardAnchor(
  scrollRoot: RefObject<HTMLElement | null>,
  order: readonly string[],
  scope: string,
): (taskId: string) => () => void {
  const anchor = useRef<CardPlace | null>(null);
  const moved = useRef<MovedCard | null>(null);
  const shown = useRef<{ order: string; scope: string } | null>(null);
  const orderRef = useRef(order);
  orderRef.current = order;
  const orderKey = order.join("|");

  const capture = useCallback(() => {
    const root = scrollRoot.current;
    if (root === null) return;
    const rootTop = root.getBoundingClientRect().top;
    const card = cardElements(root).find(
      (candidate) => candidate.getBoundingClientRect().bottom > rootTop,
    );
    anchor.current = card?.dataset.taskId
      ? { id: card.dataset.taskId, offset: offsetOf(root, card) }
      : null;
  }, [scrollRoot]);

  useEffect(() => {
    const root = scrollRoot.current;
    if (root === null) return;
    let frame: number | null = null;
    const onScroll = () => {
      if (frame !== null) return;
      frame = requestAnimationFrame(() => {
        frame = null;
        capture();
      });
    };
    root.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      root.removeEventListener("scroll", onScroll);
      if (frame !== null) cancelAnimationFrame(frame);
    };
  }, [capture, scrollRoot]);

  useEffect(() => {
    const root = scrollRoot.current;
    if (root === null || typeof ResizeObserver === "undefined") return;
    const resizes = new ResizeObserver(() => capture());
    const observeChildren = () => {
      for (const child of root.children) resizes.observe(child);
    };
    observeChildren();
    const mutations = new MutationObserver(() => {
      observeChildren();
      capture();
    });
    mutations.observe(root, { childList: true });
    return () => {
      mutations.disconnect();
      resizes.disconnect();
    };
  }, [capture, scrollRoot]);

  useLayoutEffect(() => {
    const root = scrollRoot.current;
    const before = shown.current;
    shown.current = { order: orderKey, scope };
    if (root !== null && before !== null && before.scope === scope && before.order !== orderKey) {
      const move = moved.current;
      if (move !== null && movedAway(orderRef.current, move)) {
        moved.current = null;
        restore(root, move.successor)?.focus({ preventScroll: true });
      } else if (anchor.current !== null) {
        restore(root, anchor.current);
      }
    }
    capture();
  }, [capture, orderKey, scope, scrollRoot]);

  return useCallback(
    (taskId: string) => {
      const root = scrollRoot.current;
      const ids = orderRef.current;
      const index = ids.indexOf(taskId);
      const successorId = index === -1 ? undefined : ids[index + 1];
      const card = root === null ? undefined : findCard(root, taskId);
      if (root === null || card === undefined || successorId === undefined) return () => {};
      moved.current = { id: taskId, successor: { id: successorId, offset: offsetOf(root, card) } };
      return () => {
        if (moved.current?.id === taskId) moved.current = null;
      };
    },
    [scrollRoot],
  );
}
