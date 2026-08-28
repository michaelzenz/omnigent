// HTML preview view — replaces the chat column when the user opens a
// served HTML link from an agent's response. The preview renders in a
// sandboxed iframe (no `allow-same-origin`, so the HTML executes in a
// unique origin that cannot access the app's cookies, storage, or DOM).
//
// The toolbar has: a "Preview" badge, a read-only URL bar, refresh,
// open-in-browser, and a close X that returns to chat. The iframe
// fills the remaining space and is scrollable.

import { ExternalLinkIcon, MonitorIcon, RotateCwIcon, XIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

interface HtmlPreviewViewProps {
  /** Relative API path, e.g. `/v1/sessions/conv_abc/resources/files/file_xyz/preview`. */
  url: string;
  /** Close the preview and return to chat. */
  onClose: () => void;
}

export function HtmlPreviewView({ url, onClose }: HtmlPreviewViewProps) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  // Bump key to force iframe remount on refresh (simplest reliable reload).
  const [reloadKey, setReloadKey] = useState(0);

  const handleRefresh = useCallback(() => {
    setReloadKey((k) => k + 1);
  }, []);

  const handleOpenExternal = useCallback(() => {
    window.open(url, "_blank", "noopener,noreferrer");
  }, [url]);

  // Esc closes the preview — matches the mental model of a browser tab.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      {/* Toolbar */}
      <div className="flex shrink-0 items-center gap-1.5 border-b border-border bg-card px-2 py-1.5">
        <span className="flex items-center gap-1.5 pr-1.5 text-foreground/60 text-xs font-semibold uppercase tracking-wider">
          <MonitorIcon className="size-3.5 shrink-0" />
          Preview
        </span>
        <input
          type="text"
          value={url}
          readOnly
          spellCheck={false}
          aria-label="Preview URL"
          className="h-6 min-w-0 flex-1 rounded-md border border-input bg-transparent px-2 text-foreground/70 text-xs outline-none"
        />
        <button
          type="button"
          onClick={handleRefresh}
          aria-label="Refresh preview"
          title="Refresh"
          className="flex size-6 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <RotateCwIcon className="size-3.5" />
        </button>
        <button
          type="button"
          onClick={handleOpenExternal}
          aria-label="Open in browser"
          title="Open in browser"
          className="flex size-6 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <ExternalLinkIcon className="size-3.5" />
        </button>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close preview"
          title="Close preview — back to chat"
          className={cn(
            "flex size-6 items-center justify-center rounded text-muted-foreground",
            "hover:bg-destructive hover:text-destructive-foreground",
          )}
        >
          <XIcon className="size-4" />
        </button>
      </div>

      {/* Iframe — sandboxed, scrollable */}
      <div className="min-h-0 flex-1 overflow-hidden bg-white">
        <iframe
          key={reloadKey}
          ref={iframeRef}
          src={url}
          title="HTML preview"
          // `allow-scripts` lets the HTML run JS for interactive content.
          // No `allow-same-origin`: the iframe gets a unique origin so it
          // cannot access the app's cookies, localStorage, or parent DOM.
          sandbox="allow-scripts"
          className="h-full w-full border-none"
          style={{ minHeight: "100%" }}
        />
      </div>
    </div>
  );
}
