import * as DialogPrimitive from "radix-ui/dialog";
import { XIcon } from "lucide-react";
import { SideChatPane } from "@/components/chat/SideChatPane";
import { Button } from "@/components/ui/button";
import { useCanEdit } from "@/hooks/usePermissions";
import { useSession } from "@/hooks/useSession";
import { readOnlyReasonForSessionLabels } from "@/lib/sessionCapabilities";
import { type ChatTarget, isChatOpener, useGlobalHubChat } from "./GlobalHubChat";
import { OpenSessionLink } from "./OpenSessionLink";
import { useChatPanelWidth } from "./useChatPanelWidth";

/**
 * The chat of a manager or worker session, floating over the right side of the
 * board without narrowing it. A click anywhere outside the panel closes it.
 * Mount it in a positioned board area, which the panel fills from the top to
 * the bottom.
 */
export function GlobalHubChatPanel() {
  const { target, close } = useGlobalHubChat();
  const { width, panelRef, handleProps } = useChatPanelWidth();
  return (
    <DialogPrimitive.Root
      modal={false}
      open={target !== null}
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <DialogPrimitive.Content
        ref={panelRef}
        aria-describedby={undefined}
        // The composer's menus close on Escape, so Escape leaves the panel open.
        onEscapeKeyDown={(event) => event.preventDefault()}
        // A chat button switches the panel to its own session instead.
        onInteractOutside={(event) => {
          if (isChatOpener(event.target)) event.preventDefault();
        }}
        style={{ width }}
        className="absolute inset-y-0 right-0 z-20 flex max-w-[calc(100%-2rem)] flex-col border-l border-border bg-background shadow-2xl outline-none"
        data-testid="globalhub-chat-panel"
      >
        {target && <ChatPanelBody target={target} />}
        <div
          {...handleProps}
          className="absolute inset-y-0 -left-1 w-2 cursor-col-resize hover:bg-border focus-visible:bg-ring/50 focus-visible:outline-none"
        />
      </DialogPrimitive.Content>
    </DialogPrimitive.Root>
  );
}

function ChatPanelBody({ target }: { target: ChatTarget }) {
  const canEdit = useCanEdit(target.sessionId);
  const { session } = useSession(target.sessionId);
  const readOnlyReason = readOnlyReasonForSessionLabels(session, null);
  // As in main's sub-agent tab, the composer waits for the labels that decide read-only.
  const readOnly = !canEdit || session === null || readOnlyReason !== null;
  const title = session?.title || target.title;
  return (
    <>
      <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border px-3">
        <span className="shrink-0 text-xs text-muted-foreground">{target.kind}</span>
        <DialogPrimitive.Title className="min-w-0 flex-1 truncate text-sm font-medium">
          {title}
        </DialogPrimitive.Title>
        <OpenSessionLink sessionId={target.sessionId} label={`Open ${title} full screen`} />
        <DialogPrimitive.Close asChild>
          <Button variant="ghost" size="icon-sm" aria-label="Close chat" title="Close chat">
            <XIcon aria-hidden />
          </Button>
        </DialogPrimitive.Close>
      </div>
      <div className="min-h-0 flex-1">
        <SideChatPane
          key={target.sessionId}
          childId={target.sessionId}
          fullSession
          readOnly={readOnly}
          messagePrefix={target.taskId ? `taskid: ${target.taskId}\n` : undefined}
        />
      </div>
      {readOnlyReason !== null && (
        <p className="mx-3 mb-3 shrink-0 rounded-md border border-border bg-muted/40 px-3 py-2 text-center text-sm text-muted-foreground">
          {readOnlyReason}
        </p>
      )}
    </>
  );
}
