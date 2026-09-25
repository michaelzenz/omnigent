import {
  cloneElement,
  createContext,
  Fragment,
  isValidElement,
  memo,
  useContext,
  useMemo,
  type ReactNode,
} from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

/** Lowercased, whitespace-split query tokens shared by search highlighting
 * and fixture matching. Token-AND: every token must match somewhere. */
export function tokenizeQuery(raw: string): string[] {
  return raw.trim().toLowerCase().split(/\s+/).filter(Boolean);
}

const BoardSearchTokensContext = createContext<readonly string[]>([]);

export const BoardSearchProvider = memo(function BoardSearchProvider({
  query,
  children,
}: {
  query: string;
  children: ReactNode;
}) {
  const value = useMemo(() => tokenizeQuery(query), [query]);
  return (
    <BoardSearchTokensContext.Provider value={value}>{children}</BoardSearchTokensContext.Provider>
  );
});

/** The active search tokens (lowercased; [] when not searching). */
function useSearchTokens(): readonly string[] {
  return useContext(BoardSearchTokensContext);
}

const MARK_CLASSES =
  "rounded-[2px] bg-yellow-200/80 px-0.5 text-inherit dark:bg-yellow-400/30 dark:text-yellow-100";

/** All case-insensitive [start, end) spans of `token` in `text`. */
function tokenSpans(text: string, token: string): [number, number][] {
  const lowered = text.toLowerCase();
  const spans: [number, number][] = [];
  let at = lowered.indexOf(token);
  while (at !== -1) {
    spans.push([at, at + token.length]);
    at = lowered.indexOf(token, at + token.length);
  }
  return spans;
}

/** Split `text` into alternating plain strings and matched segments (marked);
 * every token occurrence is marked, overlapping spans merge. */
function highlightText(text: string, tokens: readonly string[]): ReactNode {
  if (!tokens.length || !text) return text;
  const spans = tokens.flatMap((token) => tokenSpans(text, token)).sort((a, b) => a[0] - b[0]);
  const merged: [number, number][] = [];
  for (const [start, end] of spans) {
    const last = merged[merged.length - 1];
    if (last && start < last[1]) last[1] = Math.max(last[1], end);
    else merged.push([start, end]);
  }
  const parts: ReactNode[] = [];
  let cursor = 0;
  let seq = 0;
  for (const [start, end] of merged) {
    if (start > cursor) parts.push(text.slice(cursor, start));
    parts.push(
      <mark key={`hl-${seq++}`} className={MARK_CLASSES}>
        {text.slice(start, end)}
      </mark>,
    );
    cursor = end;
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return parts;
}

/** Highlights matches inside a plain-text node (no-op when not searching). */
export function Highlight({ text }: { text: string | null | undefined }) {
  const tokens = useSearchTokens();
  if (!text) return null;
  return <>{highlightText(text, tokens)}</>;
}

/** Recursively wraps matched substrings in a React node tree (markdown output). */
function highlightNodeChildren(children: ReactNode, tokens: readonly string[]): ReactNode {
  if (!tokens.length || children == null) return children;
  if (typeof children === "string") return highlightText(children, tokens);
  if (Array.isArray(children)) {
    // Index keys are stable here: the array is markdown-render output,
    // recreated whole on each parse, never reordered or spliced.
    return children.map((child, index) => (
      // eslint-disable-next-line react/no-array-index-key
      <Fragment key={index}>{highlightNodeChildren(child, tokens)}</Fragment>
    ));
  }
  if (isValidElement(children)) {
    // Nested highlight-aware blocks (li > p, blockquote > p) already mark
    // their own text — re-walking them would mark it twice.
    if (SELF_HIGHLIGHTED.has(children.type)) return children;
    const element = children as React.ReactElement<{ children?: ReactNode }>;
    if (element.props.children != null) {
      return cloneElement(
        element,
        undefined,
        highlightNodeChildren(element.props.children, tokens),
      );
    }
  }
  return children;
}

// Same target=_blank behavior the TaskCard previously applied, so highlighted
// markdown keeps its link semantics.
const MARKDOWN_LINK_COMPONENTS: Components = {
  a: ({ children, ...props }) => (
    <a {...props} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
};

// Block-level tags whose converted children mix raw text with inline host
// elements — the single layer where every markdown text node passes through.
// Overriding inline tags too would double-mark: their text is already reached
// by descent from these blocks.
const BLOCK_TAGS = [
  "p",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "li",
  "td",
  "th",
  "blockquote",
  "caption",
] as const;

// Set of block components above; highlightNodeChildren skips elements of
// these types because they highlight their own children.
const SELF_HIGHLIGHTED = new Set<unknown>();

function makeHighlightedBlock(Tag: (typeof BLOCK_TAGS)[number]) {
  function HighlightedBlock(props: React.ComponentPropsWithoutRef<typeof Tag>) {
    const tokens = useSearchTokens();
    const { children, ...rest } = props;
    // Union of 12 intrinsic tags: TS cannot type-check the spread against
    // each simultaneously, so relax the component type at the JSX site.
    const Component = Tag as unknown as React.ComponentType<Record<string, unknown>>;
    return <Component {...rest}>{highlightNodeChildren(children, tokens)}</Component>;
  }
  SELF_HIGHLIGHTED.add(HighlightedBlock);
  return HighlightedBlock;
}

const MARKDOWN_COMPONENTS: Components = {
  ...MARKDOWN_LINK_COMPONENTS,
  ...(Object.fromEntries(BLOCK_TAGS.map((tag) => [tag, makeHighlightedBlock(tag)])) as Components),
};

/** ReactMarkdown whose rendered text nodes get search highlighting. The
 * markdown source must reach ReactMarkdown unmodified (it only accepts a
 * string), so highlighting happens on the rendered tree via the block
 * component overrides above. */
export function HighlightedMarkdown({ children }: { children: string }) {
  if (!children) return null;
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
      {children}
    </ReactMarkdown>
  );
}
