import type { ComponentPropsWithoutRef } from "react";
import { MessageResponse } from "@/components/ai-elements/message";
import { sameServerSessionPath } from "@/components/ai-elements/streamdown-security";
import { SessionMentionPill, STREAMDOWN_LINK_CLASS } from "@/components/blocks/ChatMarkdown";
import { stripBasePath } from "@/lib/basePath";
import { cn } from "@/lib/utils";

const SESSION_PATH = /^\/c\/([A-Za-z0-9_-]+)\/?$/;

/**
 * The id of the session a link opens on the server ``page`` is served from,
 * or ``null`` for any other link.
 */
export function boardSessionId(
  href: string,
  page: { href: string; origin: string; protocol: string; host: string },
): string | null {
  let path = sameServerSessionPath(href, page);
  if (!path) {
    let url: URL;
    try {
      url = new URL(href, page.href);
    } catch {
      return null;
    }
    if (url.origin !== page.origin) return null;
    path = stripBasePath(url.pathname);
  }
  return SESSION_PATH.exec(path)?.[1] ?? null;
}

/** A markdown link: a session on this server is a pill that opens it in the app. */
function BoardMarkdownLink({
  href,
  className,
  children,
  node: _node,
  target: _target,
  rel: _rel,
  ...props
}: ComponentPropsWithoutRef<"a"> & { node?: unknown }) {
  const sessionId =
    typeof href === "string" && typeof window !== "undefined"
      ? boardSessionId(href, window.location)
      : null;
  if (sessionId) {
    return (
      <SessionMentionPill id={sessionId} isSubagent={false}>
        {children}
      </SessionMentionPill>
    );
  }
  return (
    <a
      href={href}
      className={cn(STREAMDOWN_LINK_CLASS, className)}
      data-streamdown="link"
      target="_blank"
      rel="noreferrer"
      {...props}
    >
      {children}
    </a>
  );
}

const BOARD_MARKDOWN_COMPONENTS = { a: BoardMarkdownLink };

/** Markdown that the board's agents write, such as a task overview or a system notice. */
export function BoardMarkdown({ children }: { children: string }) {
  return (
    <MessageResponse allowGitHubAttachmentImages components={BOARD_MARKDOWN_COMPONENTS}>
      {children}
    </MessageResponse>
  );
}
