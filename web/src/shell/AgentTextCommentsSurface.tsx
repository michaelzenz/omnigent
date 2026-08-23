import { Switch } from "@/components/ui/switch";
import { useAgentTextComments } from "@/hooks/useAgentTextComments";
import { useAgentTextThreads } from "@/hooks/useAgentTextThreads";
import type { AgentTextCommentsUI } from "./AgentTextCommentsContext";
import { AgentTextCommentsPanel } from "./AgentTextCommentsPanel";
import { AgentTextThreadPanel } from "./AgentTextThreadPanel";

export function AgentTextCommentsSurface({
  conversationId,
  canEdit,
  ui,
}: {
  conversationId: string;
  canEdit: boolean;
  ui: AgentTextCommentsUI;
}) {
  const batchCount = useAgentTextComments(conversationId).data?.length ?? 0;
  const threadCount = useAgentTextThreads(conversationId, "open").data?.length ?? 0;
  const activeCount = ui.mode === "batch" ? batchCount : threadCount;
  const inactiveCount = ui.mode === "batch" ? threadCount : batchCount;

  return (
    <section className="flex min-h-0 flex-1 flex-col">
      <header className="shrink-0 border-b border-border px-3 py-2.5">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold">Comments</span>
            {activeCount > 0 && (
              <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] tabular-nums">
                {activeCount}
              </span>
            )}
          </div>
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <span>
              Threaded replies
              {inactiveCount > 0 && (
                <span className="ml-1 rounded-full bg-muted px-1 text-[9px] tabular-nums">
                  {inactiveCount}
                </span>
              )}
            </span>
            <Switch
              checked={ui.mode === "threaded"}
              disabled={ui.threadedModeLoading}
              onCheckedChange={(checked) => ui.setMode(checked ? "threaded" : "batch")}
              aria-label="Threaded replies"
            />
          </label>
        </div>
        {ui.threadedModeError && (
          <p className="mt-1.5 text-[11px] leading-4 text-destructive">
            {ui.threadedModeError}
          </p>
        )}
      </header>
      {ui.mode === "batch" ? (
        <AgentTextCommentsPanel
          conversationId={conversationId}
          canEdit={canEdit}
          ui={ui}
          showHeader={false}
        />
      ) : (
        <AgentTextThreadPanel conversationId={conversationId} canEdit={canEdit} ui={ui} />
      )}
    </section>
  );
}
