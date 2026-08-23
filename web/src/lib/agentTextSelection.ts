import type { AgentTextComment, AgentTextCommentAnchor } from "@/hooks/useAgentTextComments";

interface IndexedTextNode {
  node: Text;
  start: number;
}

export interface TextNodeIndex {
  nodes: IndexedTextNode[];
  text: string;
}

function excluded(node: Text, root: HTMLElement): boolean {
  const parent = node.parentElement;
  if (!parent || !root.contains(parent)) return true;
  return (
    parent.closest(
      "button, script, style, noscript, [aria-hidden='true'], [data-comment-excluded]",
    ) != null
  );
}

export function buildTextNodeIndex(root: HTMLElement): TextNodeIndex {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes: IndexedTextNode[] = [];
  let text = "";
  let current: Node | null;
  while ((current = walker.nextNode())) {
    const node = current as Text;
    if (excluded(node, root)) continue;
    nodes.push({ node, start: text.length });
    text += node.data;
  }
  return { nodes, text };
}

function pointOffset(index: TextNodeIndex, node: Node, offset: number): number | null {
  if (node.nodeType === Node.TEXT_NODE) {
    const found = index.nodes.find((entry) => entry.node === node);
    return found ? found.start + Math.min(offset, found.node.data.length) : null;
  }
  const boundary = document.createRange();
  try {
    boundary.setStart(node, offset);
  } catch {
    return null;
  }
  for (const entry of index.nodes) {
    if (boundary.comparePoint(entry.node, 0) >= 0) return entry.start;
  }
  return index.text.length;
}

function domPoint(index: TextNodeIndex, offset: number): { node: Text; offset: number } | null {
  for (const entry of index.nodes) {
    const end = entry.start + entry.node.data.length;
    if (offset <= end) return { node: entry.node, offset: offset - entry.start };
  }
  const last = index.nodes[index.nodes.length - 1];
  return last ? { node: last.node, offset: last.node.data.length } : null;
}

function itemRoot(node: Node): HTMLElement | null {
  const element = node.nodeType === Node.ELEMENT_NODE ? (node as Element) : node.parentElement;
  return element?.closest<HTMLElement>("[data-agent-text-item-id]") ?? null;
}

export function captureAgentTextSelection(selection: Selection): AgentTextCommentAnchor | null {
  if (selection.isCollapsed || selection.rangeCount === 0) return null;
  const range = selection.getRangeAt(0);
  const startRoot = itemRoot(range.startContainer);
  const endRoot = itemRoot(range.endContainer);
  if (!startRoot || startRoot !== endRoot || startRoot.dataset.agentTextFinal !== "true")
    return null;
  const itemId = startRoot.dataset.agentTextItemId;
  if (!itemId) return null;

  const index = buildTextNodeIndex(startRoot);
  let start = pointOffset(index, range.startContainer, range.startOffset);
  let end = pointOffset(index, range.endContainer, range.endOffset);
  if (start == null || end == null || end <= start) return null;

  while (start < end && /\s/.test(index.text[start] ?? "")) start += 1;
  while (end > start && /\s/.test(index.text[end - 1] ?? "")) end -= 1;
  if (end <= start) return null;

  return {
    conversation_item_id: itemId,
    start_offset: start,
    end_offset: end,
    selected_text: index.text.slice(start, end),
    prefix_context: index.text.slice(Math.max(0, start - 48), start),
    suffix_context: index.text.slice(end, end + 48),
  };
}

function contextMatches(text: string, start: number, comment: AgentTextComment): boolean {
  const prefix = comment.prefix_context;
  const suffix = comment.suffix_context;
  return (
    (!prefix || text.slice(Math.max(0, start - prefix.length), start).endsWith(prefix)) &&
    (!suffix || text.slice(start + comment.selected_text.length).startsWith(suffix))
  );
}

export function resolveAgentTextCommentRange(
  root: HTMLElement,
  comment: AgentTextComment | AgentTextCommentAnchor,
): Range | null {
  const index = buildTextNodeIndex(root);
  let start = comment.start_offset;
  if (index.text.slice(start, comment.end_offset) !== comment.selected_text) {
    const candidates: number[] = [];
    let from = 0;
    while (from <= index.text.length) {
      const at = index.text.indexOf(comment.selected_text, from);
      if (at < 0) break;
      candidates.push(at);
      from = at + 1;
    }
    start =
      candidates.find((candidate) =>
        contextMatches(index.text, candidate, comment as AgentTextComment),
      ) ??
      candidates.sort(
        (a, b) => Math.abs(a - comment.start_offset) - Math.abs(b - comment.start_offset),
      )[0] ??
      -1;
  }
  if (start < 0) return null;
  const startPoint = domPoint(index, start);
  const endPoint = domPoint(index, start + comment.selected_text.length);
  if (!startPoint || !endPoint) return null;
  const range = document.createRange();
  try {
    range.setStart(startPoint.node, startPoint.offset);
    range.setEnd(endPoint.node, endPoint.offset);
    return range;
  } catch {
    return null;
  }
}
