import { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";

import { boardSearchRanges, useBoardSearchHighlight } from "./useBoardSearchHighlight";

const CARD_HTML =
  "<h2 data-board-search-text>Fix retries</h2>" +
  "<button>Fix it</button>" +
  "<p data-board-search-text>The <b>retr</b>ies broke again</p>";

/** jsdom has no CSS Custom Highlight API; record what the board paints instead. */
class RecordedHighlight {
  readonly ranges: Range[];

  constructor(...ranges: Range[]) {
    this.ranges = ranges;
  }
}

function Board({ words }: { words: string[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useBoardSearchHighlight(ref, words, true);
  return <div ref={ref} dangerouslySetInnerHTML={{ __html: CARD_HTML }} />;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("board search highlights", () => {
  it("finds each word only in the parts of a card that search covers", () => {
    const root = document.createElement("div");
    root.innerHTML = CARD_HTML;

    const ranges = boardSearchRanges(root, ["fix", "retries"]);

    // The button's "Fix" is not card text; "retries" spans the bold run.
    expect(ranges.map((range) => range.toString())).toEqual(["Fix", "retries", "retries"]);
  });

  it("paints the words while the board shows them, and clears them after", () => {
    const highlights = new Map<string, RecordedHighlight>();
    vi.stubGlobal("CSS", { highlights });
    vi.stubGlobal("Highlight", RecordedHighlight);

    const words = ["retries"];
    const { unmount } = render(<Board words={words} />);
    const painted = highlights.get("globalhub-board-search")?.ranges.map(String);
    unmount();

    expect(painted).toEqual(["retries", "retries"]);
    expect(highlights.has("globalhub-board-search")).toBe(false);
  });
});
