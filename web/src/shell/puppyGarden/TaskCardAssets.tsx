import { useState } from "react";
import { MessageSquareIcon, XIcon } from "lucide-react";
import { useDeleteTaskAsset } from "@/hooks/useAgentTasks";
import type { TaskAssetCategory, TaskAssetSummary, TaskWorkerLane } from "@/lib/agentTasksApi";
import { cn } from "@/lib/utils";
import { usePuppyGardenChat } from "./PuppyGardenChatContext";

interface TaskCardAssetsProps {
  taskId: string;
  assets: TaskAssetSummary[];
}

const CATEGORIES: { value: TaskAssetCategory; label: string }[] = [
  { value: "code", label: "Code" },
  { value: "tests", label: "Tests" },
  { value: "documents", label: "Documents" },
  { value: "logs", label: "Logs" },
  { value: "other", label: "Other" },
];

export function TaskCardAssets({ taskId, assets }: TaskCardAssetsProps) {
  const deleteAsset = useDeleteTaskAsset(taskId);
  if (!assets.length) return <p className="p-3 text-sm text-muted-foreground">No assets yet.</p>;

  return (
    <div className="space-y-3 p-2" data-testid="task-card-assets-list">
      {CATEGORIES.map((category) => {
        const rows = assets.filter((asset) => (asset.category ?? "other") === category.value);
        if (!rows.length) return null;
        return (
          <section key={category.value} data-testid={`task-assets-${category.value}`}>
            <h4 className="px-1 pb-1 text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">
              {category.label}
            </h4>
            <ul className="space-y-1.5">
              {rows.map((asset) => {
                const openable = asset.kind === "url" && asset.url;
                return (
                  <li
                    key={asset.id}
                    data-testid={`task-asset-${asset.id}`}
                    className="flex items-start gap-1 rounded-md border border-border/70 bg-background px-2 py-1.5 text-xs"
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
                      aria-label={`Remove ${asset.title}`}
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
          </section>
        );
      })}
    </div>
  );
}

function WorkersTab({ taskId, workers }: { taskId: string; workers: TaskWorkerLane[] }) {
  const { openWorker, isWorkerSelected } = usePuppyGardenChat();
  if (!workers.length) return <p className="p-3 text-sm text-muted-foreground">No workers yet.</p>;

  return (
    <ul className="space-y-2 p-2" data-testid="task-card-workers">
      {workers.map((worker) => {
        const label = worker.provider_name ?? "Worker";
        const selected = isWorkerSelected(taskId, worker.worker_id);
        const canOpen = Boolean(worker.target_id && worker.kind !== "external");
        return (
          <li key={worker.worker_id}>
            <button
              type="button"
              disabled={!canOpen}
              aria-label={canOpen ? `Open ${label} chat` : `${label} has no live chat yet`}
              aria-pressed={selected}
              className={cn(
                "flex w-full items-start justify-between gap-2 rounded-lg border border-border bg-background p-2 text-left",
                canOpen && "hover:border-primary/50 hover:bg-muted/40",
                selected && "border-primary ring-1 ring-primary/30",
                !canOpen && "cursor-default opacity-70",
              )}
              onClick={(event) => {
                event.stopPropagation();
                if (worker.target_id && worker.kind !== "external") {
                  openWorker(taskId, worker.worker_id, worker.target_id, label);
                }
              }}
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">{label}</span>
                <span className="line-clamp-2 block text-xs text-muted-foreground">
                  {worker.failure_reason ?? worker.situation}
                </span>
              </span>
              {canOpen ? (
                <span
                  className={cn(
                    "inline-flex size-7 shrink-0 items-center justify-center rounded-md border",
                    selected ? "border-primary bg-primary text-primary-foreground" : "border-border",
                  )}
                >
                  <MessageSquareIcon className="size-4" aria-hidden />
                </span>
              ) : null}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export function TaskCardSidebar({
  taskId,
  assets,
  workers,
}: {
  taskId: string;
  assets: TaskAssetSummary[];
  workers: TaskWorkerLane[];
}) {
  const [tab, setTab] = useState<"assets" | "workers">("assets");
  return (
    <aside
      className="min-w-0 rounded-lg border border-border bg-muted/20"
      data-testid="task-card-sidebar"
    >
      <div
        className="grid grid-cols-2 border-b border-border p-1"
        role="tablist"
        aria-label="Task details"
      >
        {(["assets", "workers"] as const).map((value) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={tab === value}
            className={cn(
              "rounded-md px-2 py-1.5 text-xs font-medium capitalize",
              tab === value
                ? "bg-background shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
            onClick={(event) => {
              event.stopPropagation();
              setTab(value);
            }}
          >
            {value}{" "}
            <span className="font-normal">
              ({value === "assets" ? assets.length : workers.length})
            </span>
          </button>
        ))}
      </div>
      <div className="max-h-[32rem] overflow-y-auto">
        {tab === "assets" ? (
          <TaskCardAssets taskId={taskId} assets={assets} />
        ) : (
          <WorkersTab taskId={taskId} workers={workers} />
        )}
      </div>
    </aside>
  );
}
