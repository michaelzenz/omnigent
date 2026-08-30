import { cjk } from "@streamdown/cjk";
import { createMathPlugin } from "@streamdown/math";
import { mermaid } from "@streamdown/mermaid";
import { defaultRehypePlugins, type LinkSafetyConfig, type StreamdownProps } from "streamdown";
import { lazyCodePlugin } from "./lazyCodePlugin";

type StreamdownRehypePlugins = NonNullable<StreamdownProps["rehypePlugins"]>;
type StreamdownRehypePlugin = StreamdownRehypePlugins[number];
type StreamdownPluginTuple = Extract<StreamdownRehypePlugin, readonly unknown[]>;
type StreamdownHardenOptions = {
  allowedImagePrefixes: string[];
  allowedLinkPrefixes?: string[];
  allowedProtocols?: string[];
  allowDataImages?: boolean;
  defaultOrigin?: string;
};
type StreamdownHardenPlugin = StreamdownPluginTuple & {
  1: StreamdownHardenOptions;
};

export const STREAMDOWN_PLUGINS = {
  cjk,
  code: lazyCodePlugin,
  // Only `$$…$$` opens math. A single `$` is prose far more often than it is a
  // math delimiter — currency ($5), rates ($/PR, $/session), shell variables
  // ($LLM_API_KEY) — and single-dollar math pairs any two of them up, rendering
  // the whole span between them as letter-by-letter math soup. Explicit TeX
  // delimiters (`\(…\)`, `\[…\]`), which is what LLMs emit for real math, are
  // rewritten to `$$…$$` by `normalizeExplicitMathDelimiters`.
  math: createMathPlugin({ singleDollarTextMath: false }),
  mermaid,
};
export const SECURE_STREAMDOWN_REHYPE_PLUGINS = createStreamdownRehypePlugins(false);
export const FILE_LINK_STREAMDOWN_REHYPE_PLUGINS = createStreamdownRehypePlugins(true);

// Attribute carrying the original, unhardened href of a workspace-file link.
export const WORKSPACE_FILE_LINK_ATTR = "data-omnigent-file";

// Attribute carrying the original href of a serve_html preview link.
export const PREVIEW_LINK_ATTR = "data-omnigent-preview";

// An href that can only be a protocol URL, a protocol-relative URL, or an
// in-page anchor, never a path to a file in the session workspace. The
// lookahead keeps a cited position off the scheme branch: `notes.md:12` is a
// filename plus a line number, but is otherwise shaped exactly like a scheme.
const NON_FILE_HREF = /^(?:[a-zA-Z][a-zA-Z0-9+.-]*:(?!\d+(?::\d+)?$)|\/\/|#)/;

// Where a file link's href is parked once the path moves to the data
// attribute. Must be a *named* fragment: harden passes a fragment-only href
// through only when `new URL(href, base).hash` round-trips, and a bare "#"
// parses to an empty hash, so it would be blocked like any unresolvable URL.
const PARKED_FILE_HREF = "#omnigent-file";

// A serve_html preview URL: the app's own same-origin HTML pane, not a file.
// The tool emits exactly this shape — a relative API path with no query or
// fragment — so an absolute spelling falls through to harden, which passes it
// and lets AppShell's click interceptor match it anyway.
const PREVIEW_HREF = /^\/v1\/sessions\/[^/?#]+\/resources\/files\/[^/?#]+\/preview$/;

// Where a preview link's href is parked while the real URL moves to the data
// attribute. Same fragment trick as PARKED_FILE_HREF: harden passes a
// fragment-only href through untouched.
const PARKED_PREVIEW_HREF = "#omnigent-preview";

interface HastElement {
  type: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: HastElement[];
}

// Streamdown enables a link-safety confirmation modal by default: clicking any
// markdown link pops an "Open external link?" dialog instead of following the
// link. Disable it so chat links behave like ordinary links — a plain click
// opens them and cmd/ctrl-click opens a new tab. With safety off Streamdown
// still renders the anchor as `<a target="_blank" rel="noreferrer">`, so we
// keep new-tab opening plus referrer/reverse-tabnabbing protection without the
// extra confirmation click.
export const CHAT_LINK_SAFETY: LinkSafetyConfig = { enabled: false };

/**
 * Moves the href of every file-path link onto {@link WORKSPACE_FILE_LINK_ATTR}
 * and parks the href on an inert fragment.
 *
 * Agents routinely link a file they just wrote (`[foo.md](/abs/ws/foo.md)`).
 * Left alone, Streamdown's harden pass either keeps such a link as a real
 * anchor (so clicking it navigates the app origin and 404s), or, for a path
 * with no leading slash, strips the href and appends " [blocked]", which
 * reads as though the app censored the link. Both are wrong: the link
 * names a workspace file and should open the FileViewer.
 *
 * Running before harden hands the path to a renderer that owns the click
 * instead. A fragment href is chosen because harden passes those through
 * untouched, while an anchor with *no* href is blocked like any other
 * unresolvable URL. Only hrefs that could name a real file are moved: a URL,
 * a `mailto:`/`javascript:` scheme, or anything carrying a query or fragment
 * is left for harden to judge exactly as before.
 *
 * serve_html preview URLs (`/v1/sessions/…/resources/files/…/preview`) are
 * path-shaped too, so without a lane of their own they'd be handed to the
 * FileViewer renderer, which can't open an API path and drops the link to
 * inert text. They are parked on {@link PREVIEW_LINK_ATTR} instead, and the
 * link renderer restores the real href so AppShell's click interceptor can
 * open the in-app preview pane.
 */
export function markWorkspaceFileLinks() {
  return (tree: HastElement) => {
    visitElements(tree, (node) => {
      if (node.tagName !== "a") return;
      const href = node.properties?.href;
      if (typeof href !== "string" || !href) return;
      if (PREVIEW_HREF.test(href)) {
        node.properties = {
          ...node.properties,
          href: PARKED_PREVIEW_HREF,
          [PREVIEW_LINK_ATTR]: href,
        };
        return;
      }
      if (NON_FILE_HREF.test(href) || href.includes("?") || href.includes("#")) return;
      node.properties = {
        ...node.properties,
        href: PARKED_FILE_HREF,
        [WORKSPACE_FILE_LINK_ATTR]: href,
      };
    });
  };
}

function visitElements(node: HastElement, visitor: (node: HastElement) => void): void {
  if (node.type === "element") visitor(node);
  for (const child of node.children ?? []) {
    visitElements(child, visitor);
  }
}

function isStreamdownHardenPlugin(
  plugin: StreamdownRehypePlugin,
): plugin is StreamdownHardenPlugin {
  return Array.isArray(plugin) && plugin.length >= 2 && isHardenOptions(plugin[1]);
}

function isHardenOptions(value: unknown): value is StreamdownHardenOptions {
  return (
    typeof value === "object" &&
    value !== null &&
    "allowedImagePrefixes" in value &&
    Array.isArray(value.allowedImagePrefixes)
  );
}

function createStreamdownRehypePlugins(markFileLinks: boolean): StreamdownRehypePlugins {
  const plugins: StreamdownRehypePlugins = [];

  for (const [key, plugin] of Object.entries(defaultRehypePlugins)) {
    if (key !== "harden") {
      plugins.push(plugin);
      continue;
    }

    // Immediately before harden, and so after Streamdown's `sanitize` step,
    // which drops the marker attribute as unknown. Sanitize has also already
    // stripped any dangerous href, leaving nothing to hand over for such a link.
    if (markFileLinks) {
      plugins.push(markWorkspaceFileLinks);
    }

    if (!isStreamdownHardenPlugin(plugin)) {
      throw new Error("Streamdown harden plugin must be a [plugin, options] tuple");
    }

    plugins.push([
      plugin[0],
      { ...plugin[1], allowedImagePrefixes: [] },
    ] satisfies StreamdownPluginTuple);
  }

  return plugins;
}
