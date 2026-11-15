import { useState } from "react";
import { ArrowUpRightIcon, ChevronRightIcon, UnlinkIcon, UsersIcon, XIcon } from "lucide-react";
import { SessionStateBadge } from "@/components/SessionStateBadge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  type AgentTaskCard,
  type AgentTaskWorker,
  useDeleteTaskAsset,
  useUntrackWorker,
} from "@/hooks/useGlobalHubTasks";
import { getSessionState } from "@/hooks/useSessionState";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { groupAssets } from "./assetGroups";
import { ConfirmDialog } from "./ConfirmDialog";

/**
 * A card's panel with the task's assets and workers, one tab each. Beside the
 * overview it fills the card's height; below it, it scrolls past a fixed height.
 */
export function TaskCardRail({ task }: { task: AgentTaskCard }) {
  return (
    <Tabs
      defaultValue="assets"
      className="max-h-80 min-h-0 flex-col gap-0 overflow-hidden rounded-lg border border-border bg-muted/20 @4xl:absolute @4xl:inset-0 @4xl:max-h-none"
      data-testid={`task-card-rail-${task.id}`}
    >
      <div className="shrink-0 border-b border-border p-1">
        <TabsList className="w-full">
          <TabsTrigger value="assets">
            Assets <span className="font-normal">({task.assets.length})</span>
          </TabsTrigger>
          <TabsTrigger value="workers">
            Workers <span className="font-normal">({task.workers.length})</span>
          </TabsTrigger>
        </TabsList>
      </div>
      <TabsContent value="assets" className="min-h-0 overflow-y-auto p-2">
        <AssetList task={task} />
      </TabsContent>
      <TabsContent value="workers" className="min-h-0 overflow-y-auto p-2">
        <WorkerList workers={task.workers} />
      </TabsContent>
    </Tabs>
  );
}

function AssetList({ task }: { task: AgentTaskCard }) {
  const remove = useDeleteTaskAsset(task.id);
  if (task.assets.length === 0) {
    return <p className="p-1 text-sm text-muted-foreground">No assets yet.</p>;
  }
  return (
    <div className="space-y-3">
      {remove.error && (
        <p role="alert" className="px-1 text-xs text-destructive">
          {remove.error.message}
        </p>
      )}
      {groupAssets(task.assets).map((group) => (
        <section key={group.category} data-testid={`task-assets-${group.category}`}>
          <h4 className="px-1 pb-1 text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">
            {group.label}
          </h4>
          <ul className="space-y-1.5">
            {group.assets.map((asset) => (
              <li
                key={asset.id}
                className="flex flex-col gap-1 rounded-md border border-border/70 bg-background px-2 py-1.5 text-xs"
                data-testid={`task-asset-${asset.id}`}
              >
                <div className="flex min-w-0 items-start gap-1">
                  <a
                    href={asset.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="min-w-0 flex-1 font-medium break-words text-primary hover:underline"
                  >
                    {asset.title}
                  </a>
                  <Button
                    variant="ghost"
                    size="icon-xxs"
                    disabled={remove.isPending}
                    aria-label={`Remove ${asset.title}`}
                    title="Remove asset"
                    onClick={() => remove.mutate(asset.id)}
                  >
                    <XIcon aria-hidden />
                  </Button>
                </div>
                <RelatedSessions sessionIds={asset.related_session_ids} workers={task.workers} />
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

/** The workers whose sessions produced or used a link, folded under a count. */
function RelatedSessions({
  sessionIds,
  workers,
}: {
  sessionIds: string[];
  workers: AgentTaskWorker[];
}) {
  const [open, setOpen] = useState(false);
  if (sessionIds.length === 0) return null;
  const related = sessionIds
    .map((sessionId) => workers.find((worker) => worker.session_id === sessionId))
    .filter((worker) => worker !== undefined);
  if (related.length === 0) {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
        <UsersIcon className="size-3 shrink-0" aria-hidden />
        all related workers removed
      </span>
    );
  }
  return (
    <>
      <button
        type="button"
        aria-expanded={open}
        title="Sessions that worked on this"
        className="flex max-w-full items-center gap-1 self-start text-[11px] text-muted-foreground hover:text-foreground"
        onClick={() => setOpen((value) => !value)}
      >
        <ChevronRightIcon
          className={cn("size-3 shrink-0 transition-transform", open && "rotate-90")}
          aria-hidden
        />
        <UsersIcon className="size-3 shrink-0" aria-hidden />
        {related.length} session{related.length === 1 ? "" : "s"}
      </button>
      {open && (
        <ul className="flex flex-col gap-1.5 rounded-md border border-dashed border-border bg-muted/30 px-2 py-1.5">
          {related.map((worker) => (
            <li key={worker.id} className="flex flex-col gap-0.5">
              <div className="flex min-w-0 items-center gap-1.5">
                <span className="min-w-0 flex-1 text-xs font-medium break-words">
                  {workerLabel(worker)}
                </span>
                <OpenSessionLink worker={worker} />
              </div>
              <span className="font-mono text-[10.5px] text-muted-foreground">
                worker {shortId(worker.id)} · session {shortId(worker.session_id)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function WorkerList({ workers }: { workers: AgentTaskWorker[] }) {
  const untrack = useUntrackWorker();
  const [confirming, setConfirming] = useState<AgentTaskWorker | null>(null);
  const close = () => setConfirming(null);
  if (workers.length === 0) {
    return <p className="p-1 text-sm text-muted-foreground">No workers yet.</p>;
  }
  return (
    <>
      <ul className="space-y-2">
        {workers.map((worker) => {
          const label = workerLabel(worker);
          const state = getSessionState(worker.session);
          return (
            <li
              key={worker.id}
              className="flex flex-col gap-1.5 rounded-lg border border-border bg-background p-2"
              data-testid={`task-worker-${worker.id}`}
            >
              <span className="text-sm font-medium break-words">{label}</span>
              <div className="flex min-w-0 items-center justify-between gap-2">
                {state ? <SessionStateBadge state={state} /> : <span />}
                <div className="flex shrink-0 items-center gap-1">
                  <OpenSessionLink worker={worker} />
                  <Button
                    variant="outline"
                    size="icon-sm"
                    className="text-muted-foreground hover:text-destructive"
                    aria-label={`Untrack ${label}`}
                    title="Untrack"
                    onClick={() => setConfirming(worker)}
                  >
                    <UnlinkIcon aria-hidden />
                  </Button>
                </div>
              </div>
            </li>
          );
        })}
      </ul>
      <ConfirmDialog
        open={confirming !== null}
        onOpenChange={(open) => {
          if (!open) close();
        }}
        title="Untrack this worker?"
        description="The session keeps running. Its next finished turn goes to the broker, which can add it to a task again."
        confirmLabel="Untrack"
        pending={untrack.isPending}
        error={untrack.error}
        onConfirm={() => {
          if (confirming) untrack.mutate(confirming.id, { onSuccess: close });
        }}
      />
    </>
  );
}

function OpenSessionLink({ worker }: { worker: AgentTaskWorker }) {
  return (
    <Button asChild variant="outline" size="icon-sm" className="text-muted-foreground">
      <Link
        to={`/c/${encodeURIComponent(worker.session_id)}`}
        aria-label={`Open ${workerLabel(worker)}`}
        title="Open session"
      >
        <ArrowUpRightIcon aria-hidden />
      </Link>
    </Button>
  );
}

function workerLabel(worker: AgentTaskWorker): string {
  return worker.title ?? worker.session.title ?? "Untitled session";
}

function shortId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 6)}…${id.slice(-4)}` : id;
}
