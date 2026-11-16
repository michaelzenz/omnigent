import { afterEach, describe, expect, it } from "vitest";
import { captureAgentTextSelection, resolveAgentTextCommentRange } from "./agentTextSelection";

function select(start: Node, startOffset: number, end: Node, endOffset: number): Selection {
  const range = document.createRange();
  range.setStart(start, startOffset);
  range.setEnd(end, endOffset);
  const selection = window.getSelection()!;
  selection.removeAllRanges();
  selection.addRange(range);
  return selection;
}

afterEach(() => {
  document.body.replaceChildren();
  window.getSelection()?.removeAllRanges();
});

describe("agent text selection anchors", () => {
  it("captures and restores a selection spanning rendered Markdown nodes", () => {
    const root = document.createElement("div");
    root.dataset.agentTextItemId = "msg_1";
    root.dataset.agentTextFinal = "true";
    root.innerHTML = "before <strong>selected</strong> after";
    document.body.append(root);

    const before = root.firstChild!;
    const selected = root.querySelector("strong")!.firstChild!;
    const anchor = captureAgentTextSelection(select(before, 3, selected, 4));

    expect(anchor).toMatchObject({
      conversation_item_id: "msg_1",
      selected_text: "ore sele",
      start_offset: 3,
      end_offset: 11,
    });
    expect(resolveAgentTextCommentRange(root, anchor!)?.toString()).toBe("ore sele");
  });

  it("rejects selections crossing agent text items", () => {
    const first = document.createElement("div");
    first.dataset.agentTextItemId = "msg_1";
    first.dataset.agentTextFinal = "true";
    first.textContent = "first";
    const second = document.createElement("div");
    second.dataset.agentTextItemId = "msg_2";
    second.dataset.agentTextFinal = "true";
    second.textContent = "second";
    document.body.append(first, second);

    expect(
      captureAgentTextSelection(select(first.firstChild!, 1, second.firstChild!, 2)),
    ).toBeNull();
  });

  it("rejects text that is still streaming", () => {
    const root = document.createElement("div");
    root.dataset.agentTextItemId = "msg_1";
    root.dataset.agentTextFinal = "false";
    root.textContent = "streaming";
    document.body.append(root);

    expect(captureAgentTextSelection(select(root.firstChild!, 0, root.firstChild!, 4))).toBeNull();
  });
});
