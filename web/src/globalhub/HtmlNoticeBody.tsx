import { useEffect, useMemo, useRef, useState } from "react";
import type { SystemNoticeValues } from "@/hooks/useGlobalHubNotice";
import { randomUUID } from "@/lib/randomUUID";
import { HTML_PREVIEW_SANDBOX } from "@/shell/codeViewerHelpers";
import { connectNoticeFrame, injectNoticeBridge } from "./noticeBridge";

interface HtmlNoticeBodyProps {
  html: string;
  /** The notice's action ids; the frame's clicks on others are dropped. */
  actionIds: ReadonlySet<string>;
  onAction: (actionId: string, inputs: SystemNoticeValues) => void;
}

/**
 * An HTML notice body in a sandboxed frame that cannot reach the app. The
 * frame's ``data-action`` buttons send their clicks to ``onAction``.
 */
export function HtmlNoticeBody({ html, actionIds, onAction }: HtmlNoticeBodyProps) {
  const [nonce] = useState(randomUUID);
  const srcDoc = useMemo(() => injectNoticeBridge(html, nonce), [html, nonce]);
  const frameRef = useRef<HTMLIFrameElement>(null);
  const portRef = useRef<MessagePort | null>(null);
  // The port's handler outlives renders, so it reads the latest props here.
  const latest = useRef({ actionIds, onAction });
  latest.current = { actionIds, onAction };
  useEffect(() => () => portRef.current?.close(), []);

  const connect = () => {
    const frame = frameRef.current?.contentWindow;
    if (!frame) return;
    portRef.current?.close();
    portRef.current = connectNoticeFrame(
      frame,
      nonce,
      (actionId) => latest.current.actionIds.has(actionId),
      (actionId, inputs) => latest.current.onAction(actionId, inputs),
    );
  };

  return (
    <div className="overflow-hidden rounded-md border border-border bg-white">
      <iframe
        ref={frameRef}
        title="System notice"
        sandbox={HTML_PREVIEW_SANDBOX}
        srcDoc={srcDoc}
        onLoad={connect}
        className="block h-80 w-full border-0"
      />
    </div>
  );
}
