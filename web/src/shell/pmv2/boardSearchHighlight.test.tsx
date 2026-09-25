import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  BoardSearchProvider,
  Highlight,
  HighlightedMarkdown,
  tokenizeQuery,
} from "./boardSearchHighlight";

afterEach(cleanup);

describe("tokenizeQuery", () => {
  it("lowercases and splits on whitespace runs", () => {
    expect(tokenizeQuery("  Fix\tAUTH\nretries ")).toEqual(["fix", "auth", "retries"]);
  });

  it("returns no tokens for blank queries", () => {
    expect(tokenizeQuery("   ")).toEqual([]);
  });
});

describe("Highlight", () => {
  it("marks every token occurrence, not just the exact phrase", () => {
    const { container } = render(
      <BoardSearchProvider query="fix auth">
        <Highlight text="Fix the auth timeout, then fix auth again" />
      </BoardSearchProvider>,
    );
    const marks = [...container.querySelectorAll("mark")];
    expect(marks.map((mark) => mark.textContent)).toEqual(["Fix", "auth", "fix", "auth"]);
  });

  it("marks nothing when no token matches", () => {
    const { container } = render(
      <BoardSearchProvider query="zzz">
        <Highlight text="Fix the auth timeout" />
      </BoardSearchProvider>,
    );
    expect(container.querySelectorAll("mark")).toHaveLength(0);
  });

  it("merges overlapping token spans", () => {
    const { container } = render(
      <BoardSearchProvider query="fix fixa">
        <Highlight text="fixauth" />
      </BoardSearchProvider>,
    );
    expect(container.querySelectorAll("mark")).toHaveLength(1);
    expect(container.querySelector("mark")?.textContent).toBe("fixa");
  });
});

describe("HighlightedMarkdown", () => {
  it("marks tokens inside markdown nodes", () => {
    const { container } = render(
      <BoardSearchProvider query="token spread">
        <HighlightedMarkdown>{"**token** counts spread out"}</HighlightedMarkdown>
      </BoardSearchProvider>,
    );
    const strongMark = container.querySelector("strong mark");
    expect(strongMark?.textContent).toBe("token");
    expect(container.querySelectorAll("mark")).toHaveLength(2);
  });

  it("does not double-mark text in nested blocks", () => {
    const { container } = render(
      <BoardSearchProvider query="token">
        <HighlightedMarkdown>{"> token **token**"}</HighlightedMarkdown>
      </BoardSearchProvider>,
    );
    expect(container.querySelectorAll("mark")).toHaveLength(2);
  });
});
