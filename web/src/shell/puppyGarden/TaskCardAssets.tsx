import { useEffect, useState } from "react";
import {
  ArrowUpRightIcon,
  ChevronDownIcon,
  FolderOpenIcon,
  MessageSquareIcon,
  XIcon,
  UnlinkIcon,
  ArrowLeftRightIcon,
} from "lucide-react";
import { Link } from "@/lib/routing";
import { Button } from "@/components/ui/button";
import { useDeleteTaskAsset, useUntrackWorker } from "@/hooks/useAgentTasks";
import type { TaskAssetCategory, TaskAssetSummary, TaskWorkerLane } from "@/lib/agentTasksApi";
import { cn } from "@/lib/utils";
import {
  getEditorCapabilities,
  getHostIdentity,
  isElectronShell,
  openProject,
  type ProjectEditor,
} from "@/lib/nativeBridge";
import { fetchSshConnections } from "@/lib/sshApi";
import type { SshConnection } from "@/lib/sshConnectionPreferences";
import { useQuery } from "@tanstack/react-query";
import { readWorkspaceEditor } from "@/lib/puppyGardenPreferences";
import { Highlight, anyTextMatches, useSearchQuery } from "./boardSearchHighlight";
import { assetProvenanceTexts, laneTexts } from "./useBoardSearch";
import { usePuppyGardenChat } from "./PuppyGardenChatContext";
import { RebindWorkerDialog } from "./RebindWorkerDialog";

interface TaskCardAssetsProps {
  taskId: string;
  assets: TaskAssetSummary[];
  /** Worker lanes, to resolve asset provenance (source worker) chips. */
  workers: TaskWorkerLane[];
  /** Session host id, for SSH-remote workspace launches. */
  hostId?: string | null;
}

const CATEGORIES: { value: TaskAssetCategory; label: string }[] = [
  { value: "workspace", label: "Workspaces" },
  { value: "code", label: "Code" },
  { value: "tests", label: "Tests" },
  { value: "documents", label: "Documents" },
  { value: "logs", label: "Logs" },
  { value: "other", label: "Other" },
];

// ---- Asset provenance ("harvested from worker") ----

const PROV_LANE_STATE_CLASSES: Record<string, string> = {
  active:
    "border-[rgba(34,197,94,0.55)] bg-[rgba(34,197,94,0.07)] text-[#15803d] dark:bg-[rgba(34,197,94,0.08)] dark:text-[#4ade80]",
  idle: "border-[rgba(100,116,139,0.45)] bg-[rgba(100,116,139,0.06)] text-[#64748b] dark:bg-[rgba(148,163,184,0.06)] dark:text-[#94a3b8]",
  new: "border-[rgba(234,179,8,0.6)] bg-[rgba(234,179,8,0.08)] text-[#a16207] dark:bg-[rgba(234,179,8,0.08)] dark:text-[#fde047]",
};

function shortId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 6)}…${id.slice(-4)}` : id;
}

/** Folded "from <worker>" chip; unfolds to the worker title, state badge,
 * jump-to-chat button, and a worker/session id line. External lanes keep the
 * chip but the jump is disabled (no omnigent chat page). */
function AssetProvenance({
  taskId,
  asset,
  workers,
}: {
  taskId: string;
  asset: TaskAssetSummary;
  workers: TaskWorkerLane[];
}) {
  const { openWorker } = usePuppyGardenChat();
  const [open, setOpen] = useState(false);
  const lane = asset.source_worker_id
    ? workers.find((worker) => worker.worker_id === asset.source_worker_id)
    : undefined;

  // Human-added assets have no provenance; nothing rendered.
  if (!asset.source_worker_id) return null;

  const label = lane ? (lane.title ?? lane.provider_name ?? "Worker") : null;
  const canOpen = Boolean(lane && lane.target_id && lane.kind !== "external");

  return (
    <>
      <button
        type="button"
        aria-expanded={open}
        className="flex max-w-full items-center gap-1 self-start text-left text-[11px] text-muted-foreground hover:text-foreground"
        onClick={(event) => {
          event.stopPropagation();
          setOpen((value) => !value);
        }}
        data-testid={`asset-prov-${asset.id}`}
      >
        <ChevronDownIcon
          className={cn("size-3 shrink-0 transition-transform", open && "rotate-90")}
          aria-hidden
        />
        <span className="shrink-0">from</span>
        <span className="min-w-0 truncate">
          {lane ? <Highlight text={label} /> : "worker removed"}
        </span>
      </button>
      {open ? (
        lane ? (
          <div
            className="flex flex-col gap-1 rounded-md border border-dashed border-border bg-muted/30 px-2 py-1.5"
            data-testid={`asset-prov-body-${asset.id}`}
          >
            <div className="flex min-w-0 items-center gap-1.5">
              <span className="min-w-0 flex-1 break-words text-xs font-medium">
                <Highlight text={label} />
              </span>
              <span
                className={cn(
                  "shrink-0 rounded-full border px-1.5 py-px text-[9.5px] font-semibold",
                  PROV_LANE_STATE_CLASSES[lane.state] ?? PROV_LANE_STATE_CLASSES.idle,
                )}
              >
                {lane.kind === "external" ? "external" : lane.state}
              </span>
              <button
                type="button"
                disabled={!canOpen}
                title={canOpen ? "Open chat" : "External sessions have no omnigent chat page"}
                className="inline-flex shrink-0 items-center gap-1 rounded-md border border-border bg-background px-1.5 py-0.5 text-[11px] text-primary hover:bg-muted/60 disabled:cursor-not-allowed disabled:text-muted-foreground disabled:hover:bg-background"
                onClick={(event) => {
                  event.stopPropagation();
                  if (canOpen && lane.target_id) {
                    openWorker(taskId, lane.worker_id, lane.target_id, label ?? "Worker");
                  }
                }}
              >
                <MessageSquareIcon className="size-3" aria-hidden />
                {canOpen ? "Open chat" : "No chat"}
              </button>
            </div>
            <div className="font-mono text-[10.5px] text-muted-foreground">
              worker {shortId(lane.worker_id)}
              {lane.target_id ? <> · session {shortId(lane.target_id)}</> : null}
            </div>
          </div>
        ) : (
          <div className="text-[10.5px] text-muted-foreground">
            This asset&apos;s source worker is no longer tracked.
          </div>
        )
      ) : null}
    </>
  );
}

export function TaskCardAssets({ taskId, assets, workers, hostId }: TaskCardAssetsProps) {
  const deleteAsset = useDeleteTaskAsset(taskId);
  const openWorkspace = useWorkspaceAssetOpener();
  const searchQuery = useSearchQuery();
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
                const isWorkspace = asset.kind === "workspace";
                const openable = asset.kind === "url" && asset.url;
                const workspaceOpenable =
                  isWorkspace && openWorkspace != null && Boolean(asset.url);
                const handleOpenWorkspace = () => {
                  if (workspaceOpenable && asset.url) openWorkspace(asset.url, hostId);
                };
                // Search highlight: ring the row when the asset matched (by
                // title or url); if only the url matched, surface the url text
                // (highlighted) so the reason for the match is visible.
                const assetMatched =
                  searchQuery !== "" &&
                  anyTextMatches(assetProvenanceTexts(asset, workers), searchQuery);
                const urlIsMatch =
                  assetMatched && asset.url != null && !anyTextMatches([asset.title], searchQuery);
                return (
                  <li
                    key={asset.id}
                    data-testid={`task-asset-${asset.id}`}
                    className={cn(
                      "flex flex-col gap-1 rounded-md border border-border/70 bg-background px-2 py-1.5 text-xs",
                      assetMatched &&
                        "border-amber-400/70 bg-amber-50/70 ring-1 ring-amber-400/50 dark:bg-amber-400/10",
                    )}
                  >
                    {workspaceOpenable ? (
                      <button
                        type="button"
                        className="flex min-w-0 flex-1 items-start gap-1.5 break-words text-left font-medium text-primary hover:underline"
                        title={`Open in ${readWorkspaceEditor() === "cursor" ? "Cursor" : "VS Code"}`}
                        onClick={(event) => {
                          event.stopPropagation();
                          handleOpenWorkspace();
                        }}
                      >
                        <FolderOpenIcon className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                        <span className="min-w-0 break-words">
                          <Highlight text={asset.title} />
                        </span>
                      </button>
                    ) : openable ? (
                      <a
                        href={asset.url ?? undefined}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="min-w-0 flex-1 break-words font-medium text-primary hover:underline"
                        onClick={(event) => event.stopPropagation()}
                      >
                        <Highlight text={asset.title} />
                      </a>
                    ) : (
                      <span className="min-w-0 flex-1 break-words font-medium">
                        <Highlight text={asset.title} />
                      </span>
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
                    {urlIsMatch && asset.url ? (
                      <span className="break-all text-[11px] text-muted-foreground">
                        <Highlight text={asset.url} />
                      </span>
                    ) : null}
                    <AssetProvenance taskId={taskId} asset={asset} workers={workers} />
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

/**
 * Launch a workspace asset in the user's configured default editor, mirroring
 * the chat page's "Open project" button: Electron shell only, editor detected
 * by the desktop shell, SSH alias resolved for remote hosts. Returns the click
 * handler, or null when the launch isn't possible (browser shell or no
 * detected editor).
 */
function useWorkspaceAssetOpener(): ((path: string, hostId?: string | null) => void) | null {
  const [capabilities, setCapabilities] = useState<{
    cursor: boolean;
    vscode: boolean;
  } | null>(null);
  const [localHostId, setLocalHostId] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);

  useEffect(() => {
    if (!isElectronShell()) return;
    void getEditorCapabilities().then((caps) => {
      if (caps) setCapabilities(caps);
    });
    void getHostIdentity().then((identity) => {
      if (identity) setLocalHostId(identity.hostId);
    });
  }, []);

  const { data: sshData } = useQuery({
    queryKey: ["ssh-connections"],
    queryFn: fetchSshConnections,
    staleTime: 30_000,
    enabled: isElectronShell(),
  });

  if (!isElectronShell() || !capabilities) return null;

  return (workspacePath: string, hostId?: string | null) => {
    if (launching) return;
    const editor: ProjectEditor = readWorkspaceEditor();
    const hasEditor = editor === "cursor" ? capabilities.cursor : capabilities.vscode;
    if (!hasEditor) return;
    const isRemote = Boolean(hostId) && (!localHostId || hostId !== localHostId);
    const conn = isRemote
      ? (sshData?.connections ?? []).find(
          (c: SshConnection) => c.hostId === (hostId ?? null) && c.status === "online",
        )
      : null;
    if (isRemote && !conn) return;
    void (async () => {
      setLaunching(true);
      try {
        const result = await openProject({
          editor,
          workspace: workspacePath,
          sshAlias: conn?.alias ?? undefined,
        });
        if (!result.ok && result.error) console.warn("open workspace failed:", result.error);
      } finally {
        setLaunching(false);
      }
    })();
  };
}

function WorkersTab({ taskId, workers }: { taskId: string; workers: TaskWorkerLane[] }) {
  const { openWorker, isWorkerSelected } = usePuppyGardenChat();
  const untrack = useUntrackWorker();
  const searchQuery = useSearchQuery();
  const [confirmUntrack, setConfirmUntrack] = useState<string | null>(null);
  const [rebindWorker, setRebindWorker] = useState<{ id: string; name: string } | null>(null);
  if (!workers.length) return <p className="p-3 text-sm text-muted-foreground">No workers yet.</p>;

  return (
    <>
      <ul className="space-y-2 p-2" data-testid="task-card-workers">
        {workers.map((worker) => {
          const label = worker.title ?? worker.provider_name ?? "Worker";
          const selected = isWorkerSelected(taskId, worker.worker_id);
          const canOpen = Boolean(worker.target_id && worker.kind !== "external");
          const matched = searchQuery !== "" && anyTextMatches(laneTexts(worker), searchQuery);
          return (
            <li key={worker.worker_id}>
              <div
                className={cn(
                  "flex w-full flex-col gap-1.5 rounded-lg border border-border bg-background p-2 text-left",
                  canOpen && "hover:border-primary/50 hover:bg-muted/40",
                  selected && "border-primary ring-1 ring-primary/30",
                  matched &&
                    "border-amber-400/70 bg-amber-50/70 ring-1 ring-amber-400/50 dark:bg-amber-400/10",
                  !canOpen && "opacity-90",
                )}
              >
                {/* Title gets its own full-width row and wraps (no truncation) so
                    manager-maintained titles are fully readable; the situation
                    notice and action buttons share the row below so a fourth
                    button never squeezes the title. */}
                <span className="block min-w-0 break-words text-sm font-medium">
                  <Highlight text={label} />
                  {worker.kind === "external" && (
                    <span className="ml-1.5 inline-block rounded-full bg-violet-100 px-1.5 py-0.5 text-[10px] font-semibold text-violet-700 dark:bg-violet-950 dark:text-violet-300">
                      external
                    </span>
                  )}
                </span>
                <span className="flex min-w-0 items-center justify-between gap-2">
                  <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                    {worker.failure_reason ?? worker.situation}
                  </span>
                  <span className="flex shrink-0 items-center gap-1">
                    {canOpen ? (
                      <>
                        <Link
                          to={`/c/${worker.target_id}`}
                          aria-label={`Open ${label} chat page`}
                          title="Open chat page"
                          className="inline-flex size-7 items-center justify-center rounded-md border border-border text-muted-foreground hover:border-primary/50 hover:text-foreground"
                          onClick={(event) => event.stopPropagation()}
                        >
                          <ArrowUpRightIcon className="size-4" aria-hidden />
                        </Link>
                        <button
                          type="button"
                          aria-label={`Open ${label} chat`}
                          className={cn(
                            "inline-flex size-7 items-center justify-center rounded-md border",
                            selected
                              ? "border-primary bg-primary text-primary-foreground"
                              : "border-border",
                          )}
                          onClick={(event) => {
                            event.stopPropagation();
                            if (worker.target_id && worker.kind !== "external") {
                              openWorker(taskId, worker.worker_id, worker.target_id, label);
                            }
                          }}
                        >
                          <MessageSquareIcon className="size-4" aria-hidden />
                        </button>
                      </>
                    ) : null}
                    <button
                      type="button"
                      title="Rebind to another task"
                      aria-label={`Rebind ${label}`}
                      className="inline-flex size-7 items-center justify-center rounded-md border border-border text-muted-foreground hover:border-blue-300 hover:bg-blue-50 hover:text-blue-600 dark:hover:bg-blue-950"
                      onClick={(event) => {
                        event.stopPropagation();
                        setRebindWorker({ id: worker.worker_id, name: label });
                      }}
                    >
                      <ArrowLeftRightIcon className="size-4" aria-hidden />
                    </button>
                    <button
                      type="button"
                      title="Untrack"
                      aria-label={`Untrack ${label}`}
                      className="inline-flex size-7 items-center justify-center rounded-md border border-border text-muted-foreground hover:border-red-300 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950"
                      onClick={(event) => {
                        event.stopPropagation();
                        setConfirmUntrack(worker.worker_id);
                      }}
                    >
                      <UnlinkIcon className="size-4" aria-hidden />
                    </button>
                  </span>
                </span>
              </div>
            </li>
          );
        })}
      </ul>
      {confirmUntrack && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/20"
          onClick={() => setConfirmUntrack(null)}
        >
          <div
            className="w-[360px] max-w-[90vw] rounded-xl border border-border bg-background p-6 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <p className="text-sm text-foreground">Untrack this worker from the task?</p>
            <p className="mt-1 text-xs text-muted-foreground">
              The session keeps running but won't route updates here.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => setConfirmUntrack(null)}>
                Cancel
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="border-red-300 text-red-600 hover:bg-red-50"
                disabled={untrack.isPending}
                onClick={() => {
                  untrack.mutate(confirmUntrack, {
                    onSuccess: () => setConfirmUntrack(null),
                  });
                }}
              >
                Untrack
              </Button>
            </div>
          </div>
        </div>
      )}
      {rebindWorker && (
        <RebindWorkerDialog
          workerId={rebindWorker.id}
          workerName={rebindWorker.name}
          currentTaskId={taskId}
          onClose={() => setRebindWorker(null)}
        />
      )}
    </>
  );
}

export function TaskCardSidebar({
  taskId,
  assets,
  workers,
  hostId,
}: {
  taskId: string;
  assets: TaskAssetSummary[];
  workers: TaskWorkerLane[];
  hostId?: string | null;
}) {
  const [tab, setTab] = useState<"assets" | "workers">("assets");
  return (
    <aside
      // Grid default stretch: the rail's bottom always lands on the card
      // bottom (the row height comes from the tallest sibling). Two grid
      // rows — pinned tab strip, then the scroll viewport filling ALL the
      // remaining height — so the visible scroll area equals the rail and
      // there is no blank strip between the last row and the bottom border.
      className="grid min-h-0 min-w-0 grid-rows-[auto_minmax(0,1fr)] rounded-lg border border-border bg-muted/20"
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
      <div className="min-h-0 overflow-y-auto">
        {tab === "assets" ? (
          <TaskCardAssets taskId={taskId} assets={assets} workers={workers} hostId={hostId} />
        ) : (
          <WorkersTab taskId={taskId} workers={workers} />
        )}
      </div>
    </aside>
  );
}
