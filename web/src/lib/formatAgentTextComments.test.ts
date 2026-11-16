import { describe, expect, it } from "vitest";
import type { AgentTextComment } from "@/hooks/useAgentTextComments";
import { formatAgentTextComments } from "./formatAgentTextComments";

function comment(id: string, selectedText: string, body: string): AgentTextComment {
  return {
    id,
    conversation_id: "conv_1",
    conversation_item_id: `msg_${id}`,
    start_offset: 0,
    end_offset: selectedText.length,
    selected_text: selectedText,
    prefix_context: "",
    suffix_context: "",
    body,
    created_at: 1,
    updated_at: 1,
  };
}

describe("formatAgentTextComments", () => {
  it("formats the review batch as one ordered user message", () => {
    expect(
      formatAgentTextComments([
        comment("one", "first line\nsecond line", "Fix this."),
        comment("two", "another claim", "Explain why."),
      ]),
    ).toBe(`1. Agent text:
> first line
> second line

Comment:
Fix this.

2. Agent text:
> another claim

Comment:
Explain why.`);
  });
});
