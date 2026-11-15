import { type RefObject, useEffect } from "react";
import { findTextRanges, highlightsSupported } from "@/shell/previewSearch";

/** The CSS highlight that paints board search words; styled by `BOARD_SEARCH_HIGHLIGHT_CSS`. */
const HIGHLIGHT_NAME = "globalhub-board-search";

export const BOARD_SEARCH_HIGHLIGHT_CSS = `::highlight(${HIGHLIGHT_NAME}) { background-color: color-mix(in oklch, var(--warning) 35%, transparent); }`;

/** The ranges of each word inside the parts of ``root`` marked ``data-board-search-text``. */
export function boardSearchRanges(root: Element, words: readonly string[]): Range[] {
  return [...root.querySelectorAll("[data-board-search-text]")].flatMap((element) =>
    words.flatMap((word) => findTextRanges(element, word)),
  );
}

/**
 * Paints the search words in the board's card text with the CSS Custom
 * Highlight API, which leaves React's nodes alone, and paints again when the
 * cards' text changes.
 *
 * @param rootRef - The element that holds the cards.
 * @param words - The search words; pass a stable array, as from `useMemo`.
 * @param ready - Whether ``rootRef`` holds the cards yet.
 */
export function useBoardSearchHighlight(
  rootRef: RefObject<Element | null>,
  words: readonly string[],
  ready: boolean,
): void {
  useEffect(() => {
    const root = rootRef.current;
    if (!ready || root === null || words.length === 0 || !highlightsSupported()) return;
    let frame = 0;
    const paint = () => {
      frame = 0;
      CSS.highlights.set(HIGHLIGHT_NAME, new Highlight(...boardSearchRanges(root, words)));
    };
    paint();
    const observer = new MutationObserver(() => {
      if (frame === 0) frame = requestAnimationFrame(paint);
    });
    observer.observe(root, { childList: true, subtree: true, characterData: true });
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
      CSS.highlights.delete(HIGHLIGHT_NAME);
    };
  }, [rootRef, words, ready]);
}
