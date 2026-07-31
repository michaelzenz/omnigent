import { ExternalLinkIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { TaskAssetSummary } from "@/lib/agentTasksApi";
import { TASK_CARD_ASSETS_WIDTH_CLASS, TASK_CARD_SCROLLABLE_LIST_CLASS } from "./taskCardUtils";

interface TaskCardAssetsProps {
  assets: TaskAssetSummary[];
}

export function TaskCardAssets({ assets }: TaskCardAssetsProps) {
  return (
    <aside
      className={cn(
        "flex min-h-full shrink-0 flex-col self-stretch overflow-hidden border-l border-border bg-muted/20",
        TASK_CARD_ASSETS_WIDTH_CLASS,
      )}
      data-testid="task-card-assets"
    >
      <div className="shrink-0 border-b border-border px-3 py-2">
        <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Assets
          {assets.length > 0 ? (
            <span className="ml-2 font-normal text-muted-foreground normal-case">
              ({assets.length})
            </span>
          ) : null}
        </h3>
      </div>
      {assets.length === 0 ? (
        <p className="flex-1 p-3 text-xs text-muted-foreground">No assets yet.</p>
      ) : (
        <ul
          className={cn("min-h-0 flex-1 space-y-1.5 p-2", TASK_CARD_SCROLLABLE_LIST_CLASS)}
          data-testid="task-card-assets-list"
        >
          {assets.map((asset) => (
            <li key={asset.id} data-testid={`task-asset-${asset.id}`}>
              {asset.kind === "url" && asset.url ? (
                <a
                  href={asset.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-start gap-1.5 rounded-md border border-border/70 bg-background px-2 py-1.5 text-xs hover:bg-muted/60"
                >
                  <ExternalLinkIcon className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 break-words font-medium text-primary">{asset.title}</span>
                </a>
              ) : (
                <span className="block rounded-md border border-border/70 bg-background px-2 py-1.5 text-xs">
                  {asset.title}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}
