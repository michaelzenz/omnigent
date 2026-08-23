import type { AgentTextComment } from "@/hooks/useAgentTextComments";

function quote(text: string): string {
  return text
    .trim()
    .split("\n")
    .map((line) => `> ${line}`)
    .join("\n");
}

export function formatAgentTextComments(comments: AgentTextComment[]): string {
  const sections = comments.map(
    (comment, index) =>
      `${index + 1}. Agent text:\n${quote(comment.selected_text)}\n\nComment:\n${comment.body.trim()}`,
  );
  return `${sections.join("\n\n")}`;
}
