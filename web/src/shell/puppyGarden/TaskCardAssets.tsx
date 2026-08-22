import { XIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { useDeleteTaskAsset } from "@/hooks/useAgentTasks";
import type { TaskAssetSummary } from "@/lib/agentTasksApi";
import { TASK_CARD_ASSETS_WIDTH_CLASS, TASK_CARD_SCROLLABLE_LIST_CLASS } from "./taskCardUtils";

interface TaskCardAssetsProps {
  taskId: string;
  assets: TaskAssetSummary[];
}

export function TaskCardAssets({ taskId, assets }: TaskCardAssetsProps) {
  const deleteAsset = useDeleteTaskAsset(taskId);

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
          {assets.map((asset) => {
            const openable = asset.kind === "url" && asset.url;
            return (
              <li
                key={asset.id}
                data-testid={`task-asset-${asset.id}`}
                className="flex items-start gap-1 rounded-md border border-border/70 bg-background px-2 py-1.5 text-xs hover:bg-muted/60"
              >
                {openable ? (
                  <a
                    href={asset.url ?? undefined}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="min-w-0 flex-1 break-words font-medium text-primary hover:underline"
                    onClick={(event) => event.stopPropagation()}
                  >
                    {asset.title}
                  </a>
                ) : (
                  <span className="min-w-0 flex-1 break-words font-medium">{asset.title}</span>
                )}
                <button
                  type="button"
                  aria-label="Remove asset"
                  title="Remove asset"
                  className="mt-0.5 shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
                  disabled={deleteAsset.isPending}
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    deleteAsset.mutate(asset.id);
                  }}
                  data-testid={`task-asset-remove-${asset.id}`}
                >
                  <XIcon className="size-3.5" />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );
}
