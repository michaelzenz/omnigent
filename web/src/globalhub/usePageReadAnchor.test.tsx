import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useRef } from "react";

import { type PageReadEdge, usePageReadAnchor } from "./usePageReadAnchor";

const CARD_HEIGHT = 100;

interface MarkAnchor {
  current: ((edge: PageReadEdge) => void) | null;
}

function Cards({
  cards,
  reading,
  mark,
}: {
  cards: readonly string[];
  reading: boolean;
  mark: MarkAnchor;
}) {
  const scrollRoot = useRef<HTMLDivElement>(null);
  const cardsRef = useRef<HTMLDivElement>(null);
  mark.current = usePageReadAnchor(scrollRoot, cardsRef, reading);
  return (
    <div ref={scrollRoot} data-testid="scroll-root">
      <div ref={cardsRef}>
        {cards.map((card) => (
          <div key={card} />
        ))}
      </div>
    </div>
  );
}

/** Lays each card out one card height below the one before it, in the scroll root. */
function layOutCards(root: HTMLElement): void {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
    this: HTMLElement,
  ) {
    const index = Array.from(this.parentElement?.children ?? []).indexOf(this);
    return { top: index * CARD_HEIGHT - root.scrollTop } as DOMRect;
  });
}

/**
 * Reads a page that turns the ``shown`` cards into ``loaded`` while the list
 * scrolls at ``scrollTop``, and returns where the list scrolls after it.
 * ``scrollWhileReading`` scrolls as the browser or the user would meanwhile.
 */
function readPage({
  edge,
  shown,
  loaded,
  scrollTop,
  scrollWhileReading,
}: {
  edge: PageReadEdge;
  shown: readonly string[];
  loaded: readonly string[];
  scrollTop: number;
  scrollWhileReading?: (root: HTMLElement) => void;
}): number {
  const mark: MarkAnchor = { current: null };
  const { rerender } = render(<Cards cards={shown} reading={false} mark={mark} />);
  const root = screen.getByTestId("scroll-root");
  root.scrollTop = scrollTop;
  layOutCards(root);

  mark.current?.(edge);
  rerender(<Cards cards={shown} reading mark={mark} />);
  scrollWhileReading?.(root);
  rerender(<Cards cards={loaded} reading={false} mark={mark} />);
  return root.scrollTop;
}

const PAGE_ABOVE = { edge: "first", shown: ["c3"], loaded: ["c1", "c2", "c3"] } as const;

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("usePageReadAnchor", () => {
  it("scrolls down by a page that loads above the cards at the top of the list", () => {
    expect(readPage({ ...PAGE_ABOVE, scrollTop: 0 })).toBe(2 * CARD_HEIGHT);
  });

  it("scrolls down by a page that loads above the cards when the browser anchors above them", () => {
    expect(readPage({ ...PAGE_ABOVE, scrollTop: 50 })).toBe(50 + 2 * CARD_HEIGHT);
  });

  it("leaves the list where the browser anchored it on a card", () => {
    const anchored = 50 + 2 * CARD_HEIGHT;
    const scrollWhileReading = (root: HTMLElement) => {
      root.scrollTop = anchored;
    };

    expect(readPage({ ...PAGE_ABOVE, scrollTop: 50, scrollWhileReading })).toBe(anchored);
  });

  it("scrolls up by the page that drops above the cards when a page loads below them", () => {
    const pageBelow = {
      edge: "last",
      shown: ["c1", "c2", "c3"],
      loaded: ["c2", "c3", "c4"],
    } as const;

    expect(readPage({ ...pageBelow, scrollTop: 150 })).toBe(150 - CARD_HEIGHT);
  });

  it("keeps a scroll the user makes while the page loads", () => {
    const scrollWhileReading = (root: HTMLElement) => {
      root.scrollTop = 30;
      fireEvent.scroll(root);
    };

    expect(readPage({ ...PAGE_ABOVE, scrollTop: 0, scrollWhileReading })).toBe(
      30 + 2 * CARD_HEIGHT,
    );
  });
});
