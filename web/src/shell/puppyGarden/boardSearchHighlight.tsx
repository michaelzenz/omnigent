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

/** Normalized (trimmed, lowercased) query shared by search and highlighting. */
export function normalizeQuery(raw: string): string {
  return raw.trim().toLowerCase();
}

const BoardSearchQueryContext = createContext("");

export const BoardSearchProvider = memo(function BoardSearchProvider({
  query,
  children,
}: {
  query: string;
  children: ReactNode;
}) {
  const value = useMemo(() => normalizeQuery(query), [query]);
  return (
    <BoardSearchQueryContext.Provider value={value}>{children}</BoardSearchQueryContext.Provider>
  );
});

/** The active search query (normalized; "" when not searching). */
export function useSearchQuery(): string {
  return useContext(BoardSearchQueryContext);
}

/** Case-insensitive substring test; empty query never matches. */
export function textMatches(text: string | null | undefined, query: string): boolean {
  if (!query || !text) return false;
  return text.toLowerCase().includes(query);
}

/** Whether any of the given strings matches the active search query. */
export function anyTextMatches(texts: (string | null | undefined)[], query: string): boolean {
  return texts.some((text) => textMatches(text, query));
}

const MARK_CLASSES =
  "rounded-[2px] bg-yellow-200/80 px-0.5 text-inherit dark:bg-yellow-400/30 dark:text-yellow-100";

/** Split `text` into alternating plain strings and matched segments (marked). */
function highlightString(text: string, query: string): ReactNode {
  if (!query || !text) return text;
  const lowered = text.toLowerCase();
  if (!lowered.includes(query)) return text;
  const parts: ReactNode[] = [];
  let cursor = 0;
  let at = lowered.indexOf(query);
  let seq = 0;
  while (at !== -1) {
    if (at > cursor) parts.push(text.slice(cursor, at));
    parts.push(
      <mark key={`hl-${seq++}`} className={MARK_CLASSES}>
        {text.slice(at, at + query.length)}
      </mark>,
    );
    cursor = at + query.length;
    at = lowered.indexOf(query, cursor);
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return parts;
}

/** Highlights matches inside a plain-text node (no-op when not searching). */
export function Highlight({ text }: { text: string | null | undefined }) {
  const query = useSearchQuery();
  if (!text) return null;
  return <>{highlightString(text, query)}</>;
}

/** Recursively wraps matched substrings in a React node tree (markdown output). */
function highlightNodeChildren(children: ReactNode, query: string): ReactNode {
  if (!query || children == null) return children;
  if (typeof children === "string") return highlightString(children, query);
  if (Array.isArray(children)) {
    // Index keys are stable here: the array is markdown-render output,
    // recreated whole on each parse, never reordered or spliced.
    return children.map((child, index) => (
      // eslint-disable-next-line react/no-array-index-key
      <Fragment key={index}>{highlightNodeChildren(child, query)}</Fragment>
    ));
  }
  if (isValidElement(children)) {
    const element = children as React.ReactElement<{ children?: ReactNode }>;
    if (element.props.children != null) {
      return cloneElement(element, undefined, highlightNodeChildren(element.props.children, query));
    }
  }
  return children;
}

// Same target=_blank behavior the TaskCard previously applied, so highlighted
// markdown keeps its link semantics.
const MARKDOWN_COMPONENTS: Components = {
  a: ({ children, ...props }) => (
    <a {...props} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
};

/** ReactMarkdown whose rendered text nodes get search highlighting. */
export function HighlightedMarkdown({ children }: { children: string }) {
  const query = useSearchQuery();
  if (!children) return null;
  const content = (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
      {children}
    </ReactMarkdown>
  );
  if (!query) return content;
  return <>{highlightNodeChildren(content, query)}</>;
}
