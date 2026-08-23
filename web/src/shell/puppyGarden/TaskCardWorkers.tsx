import { useEffect, useMemo, useState } from "react";
import { CheckIcon, ChevronDownIcon, ChevronRightIcon, PlusIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useAssignTaskItemWorker, useCreateTaskItem } from "@/hooks/useAgentTasks";
import { useWorkerProviders } from "@/hooks/useWorkerProviders";
import {
  acquireTaskItemEditLease,
  releaseTaskItemEditLease,
  type TaskDashboard,
  type TaskItemSummary,
  type TaskWorkerLane,
} from "@/lib/agentTasksApi";
import { TaskCardItemEditor } from "./TaskCardItemEditor";
import { TaskCardItemStateBadge } from "./TaskCardItemStateBadge";
import { TaskCardRowActions } from "./TaskCardRowActions";
import { isPuppyGardenFixtureMode } from "./fixtures/puppyGardenFixtureMode";
import { isEditableItemState } from "./taskCardUtils";

const ACTIVE_STATES = new Set(["pending", "queued", "running", "interrupted", "dispatch_failed"]);

function collectFallbackItems(dashboard: TaskDashboard): TaskItemSummary[] {
  const byId = new Map<string, TaskItemSummary>();
  for (const item of dashboard.inbox_items) byId.set(item.id, item);
  for (const lane of dashboard.workers) {
    for (const row of lane.rows) {
      const item = row.kind === "item" ? row.item : row.execution.item;
      if (item) byId.set(item.id, item);
    }
    for (const execution of lane.executions) {
      if (execution.item) byId.set(execution.item.id, execution.item);
    }
  }
  return [...byId.values()];
}

function newestFirst(items: TaskItemSummary[]): TaskItemSummary[] {
  return [...items].sort(
    (a, b) =>
      (b.updated_at ?? b.created_at) - (a.updated_at ?? a.created_at) || b.id.localeCompare(a.id),
  );
}

export function taskItemsForScope(
  dashboard: TaskDashboard,
  selectedWorkerId: string | null,
): { active: TaskItemSummary[]; done: TaskItemSummary[] } {
  const fallback = collectFallbackItems(dashboard);
  const activeSource =
    dashboard.active_items ?? fallback.filter((item) => ACTIVE_STATES.has(item.state));
  const active = activeSource.filter(
    (item) =>
      item.state !== "cancelled" && (!selectedWorkerId || item.worker_id === selectedWorkerId),
  );

  const bounded = selectedWorkerId
    ? dashboard.recent_done_items?.by_worker[selectedWorkerId]
    : dashboard.recent_done_items?.all;
  const doneSource = bounded ?? fallback.filter((item) => item.state === "done");
  const done = newestFirst(
    doneSource.filter(
      (item) => item.state === "done" && (!selectedWorkerId || item.worker_id === selectedWorkerId),
    ),
  ).slice(0, 3);
  return { active, done };
}

interface WorkerPickerProps {
  taskId: string;
  item: TaskItemSummary;
  workers: TaskWorkerLane[];
}

function WorkerPicker({ taskId, item, workers }: WorkerPickerProps) {
  const assign = useAssignTaskItemWorker(taskId);
  const { data: providers = [] } = useWorkerProviders();
  const [choice, setChoice] = useState(item.worker_id ?? "");
  const [newProviderId, setNewProviderId] = useState<string | null>(null);
  const inferred = workers.find((worker) => worker.host_id && worker.workspace);
  const [hostId, setHostId] = useState(inferred?.host_id ?? "");
  const [workspace, setWorkspace] = useState(inferred?.workspace ?? "");
  const [pickerOpen, setPickerOpen] = useState(item.state !== "queued");
  const [editLeaseToken, setEditLeaseToken] = useState<string | null>(null);
  const [leaseError, setLeaseError] = useState<string | null>(null);

  useEffect(() => {
    if (!editLeaseToken || isPuppyGardenFixtureMode()) return;
    const heartbeat = window.setInterval(() => {
      void acquireTaskItemEditLease(item.id, editLeaseToken).catch(() => {
        setEditLeaseToken(null);
        setPickerOpen(false);
        setLeaseError("The edit lease expired because the item started dispatching.");
      });
    }, 45_000);
    return () => {
      window.clearInterval(heartbeat);
      void releaseTaskItemEditLease(item.id, editLeaseToken).catch(() => undefined);
    };
  }, [editLeaseToken, item.id]);

  const closePicker = async () => {
    const token = editLeaseToken;
    setPickerOpen(item.state !== "queued");
    setEditLeaseToken(null);
    setNewProviderId(null);
    if (token && !isPuppyGardenFixtureMode()) {
      await releaseTaskItemEditLease(item.id, token).catch(() => undefined);
    }
  };

  const openPicker = async () => {
    if (item.state !== "queued") {
      setPickerOpen(true);
      return;
    }
    setLeaseError(null);
    if (isPuppyGardenFixtureMode()) {
      setEditLeaseToken("fixture");
      setPickerOpen(true);
      return;
    }
    try {
      const lease = await acquireTaskItemEditLease(item.id);
      setEditLeaseToken(lease.token);
      setPickerOpen(true);
    } catch (error) {
      setLeaseError(error instanceof Error ? error.message : "Item started dispatching");
    }
  };

  const assignExisting = async (workerId: string) => {
    setChoice(workerId);
    setNewProviderId(null);
    try {
      await assign.mutateAsync({
        item_id: item.id,
        worker_id: workerId,
        edit_lease_token: editLeaseToken ?? undefined,
      });
    } finally {
      await closePicker();
    }
  };

  if (!pickerOpen) {
    return (
      <div className="space-y-1.5" onClick={(event) => event.stopPropagation()}>
        <p className="text-xs text-muted-foreground">
          {item.worker_id
            ? workers.find((worker) => worker.worker_id === item.worker_id)?.provider_name ?? "Assigned"
            : "No worker specified"}
        </p>
        <Button type="button" size="sm" variant="outline" onClick={() => void openPicker()}>
          Change worker
        </Button>
        {leaseError ? <p className="text-xs text-destructive">{leaseError}</p> : null}
      </div>
    );
  }

  return (
    <div className="space-y-2" onClick={(event) => event.stopPropagation()}>
      <label className="block text-xs text-muted-foreground">
        <span className="sr-only">Assigned worker</span>
        <select
          className="h-8 w-full rounded-md border border-input bg-background px-2 text-xs"
          value={newProviderId ? `provider:${newProviderId}` : choice}
          disabled={assign.isPending}
          onChange={(event) => {
            const value = event.target.value;
            if (value.startsWith("provider:")) {
              setNewProviderId(value.slice("provider:".length));
              return;
            }
            if (value) void assignExisting(value);
          }}
        >
          {!item.worker_id ? (
            <option value="" disabled>
              No worker specified
            </option>
          ) : null}
          <optgroup label="Assign worker">
            {workers.map((worker) => (
              <option key={worker.worker_id} value={worker.worker_id}>
                {worker.provider_name ?? "Worker"}
              </option>
            ))}
          </optgroup>
          {providers.some((provider) => provider.available) ? (
            <optgroup label="New worker">
              {providers
                .filter((provider) => provider.available)
                .map((provider) => (
                  <option key={provider.id} value={`provider:${provider.id}`}>
                    {provider.name}
                  </option>
                ))}
            </optgroup>
          ) : null}
        </select>
      </label>
      {newProviderId ? (
        <div className="space-y-2 rounded-md border border-border bg-muted/30 p-2">
          <Input
            className="h-8 text-xs"
            value={hostId}
            placeholder="Host ID"
            onChange={(event) => setHostId(event.target.value)}
          />
          <Input
            className="h-8 text-xs"
            value={workspace}
            placeholder="Workspace"
            onChange={(event) => setWorkspace(event.target.value)}
          />
          <div className="flex justify-end gap-1.5">
            <Button type="button" size="sm" variant="ghost" onClick={() => void closePicker()}>
              Cancel
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={!hostId.trim() || !workspace.trim() || assign.isPending}
              onClick={async () => {
                try {
                  await assign.mutateAsync({
                    item_id: item.id,
                    provider_id: newProviderId,
                    host_id: hostId.trim(),
                    workspace: workspace.trim(),
                    edit_lease_token: editLeaseToken ?? undefined,
                  });
                } finally {
                  await closePicker();
                }
              }}
            >
              Create & assign
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function NewTaskItem({ taskId, onClose }: { taskId: string; onClose: () => void }) {
  const createItem = useCreateTaskItem(taskId);
  const [title, setTitle] = useState("");
  const [instructions, setInstructions] = useState("");

  return (
    <div className="space-y-2 rounded-lg border border-primary/30 bg-primary/5 p-3">
      <Input
        autoFocus
        value={title}
        placeholder="Task item title"
        onChange={(event) => setTitle(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
        }}
      />
      <Textarea
        value={instructions}
        placeholder="Instructions"
        rows={3}
        onChange={(event) => setInstructions(event.target.value)}
      />
      <div className="flex justify-end gap-2">
        <Button type="button" size="sm" variant="ghost" onClick={onClose}>
          <XIcon aria-hidden /> Cancel
        </Button>
        <Button
          type="button"
          size="sm"
          disabled={!title.trim() || createItem.isPending}
          onClick={async () => {
            await createItem.mutateAsync({
              title: title.trim(),
              instructions: instructions.trim() || null,
              state: "pending",
            });
            onClose();
          }}
        >
          <CheckIcon aria-hidden /> Add item
        </Button>
      </div>
    </div>
  );
}

function ItemRow({
  taskId,
  item,
  workers,
}: {
  taskId: string;
  item: TaskItemSummary;
  workers: TaskWorkerLane[];
}) {
  const worker = workers.find((lane) => lane.worker_id === item.worker_id);
  const editable = isEditableItemState(item.state);
  return (
    <li className="space-y-2 rounded-lg border border-border bg-background p-3 shadow-xs">
      <div className="flex min-w-0 items-start justify-between gap-2">
        <h4 className="min-w-0 flex-1 text-sm leading-snug font-semibold">{item.title}</h4>
        <TaskCardItemStateBadge state={item.state} />
      </div>
      {editable ? (
        <TaskCardItemEditor
          taskId={taskId}
          item={item}
          workerLanes={workers}
          workerKind={worker?.kind ?? "managed"}
          mode={item.state === "pending" ? "ack" : item.state === "queued" ? "edit" : "parked"}
        />
      ) : (
        <>
          {item.description ? (
            <p className="text-xs whitespace-pre-wrap">{item.description}</p>
          ) : null}
          {item.instructions ? (
            <p className="text-xs whitespace-pre-wrap text-muted-foreground">{item.instructions}</p>
          ) : null}
        </>
      )}
      {editable ? <WorkerPicker taskId={taskId} item={item} workers={workers} /> : null}
      <TaskCardRowActions taskId={taskId} item={item} showStop={item.state === "running"} />
    </li>
  );
}

export function TaskItemsPanel({
  taskId,
  dashboard,
  selectedWorkerId,
}: {
  taskId: string;
  dashboard: TaskDashboard;
  selectedWorkerId: string | null;
}) {
  const [adding, setAdding] = useState(false);
  const [expandedRecentDoneScope, setExpandedRecentDoneScope] = useState<string | null>(null);
  const recentDoneScope = `${taskId}:${selectedWorkerId ?? "all"}`;
  const recentDoneExpanded = expandedRecentDoneScope === recentDoneScope;
  const { active, done } = useMemo(
    () => taskItemsForScope(dashboard, selectedWorkerId),
    [dashboard, selectedWorkerId],
  );
  const selectedWorker = dashboard.workers.find((worker) => worker.worker_id === selectedWorkerId);

  return (
    <section
      className="min-w-0 space-y-3"
      data-testid="task-items-panel"
      onClick={(event) => event.stopPropagation()}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
            Task Items
          </h3>
          {selectedWorkerId ? (
            <p className="truncate text-xs text-muted-foreground">
              Filtered to {selectedWorker?.provider_name ?? "selected worker"}
            </p>
          ) : null}
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label="Add task item"
          title="Add task item"
          onClick={(event) => {
            event.stopPropagation();
            setAdding(true);
          }}
        >
          <PlusIcon aria-hidden />
        </Button>
      </div>
      {adding ? <NewTaskItem taskId={taskId} onClose={() => setAdding(false)} /> : null}
      {active.length ? (
        <ul className="space-y-2">
          {active.map((item) => (
            <ItemRow key={item.id} taskId={taskId} item={item} workers={dashboard.workers} />
          ))}
        </ul>
      ) : (
        <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
          No active items in this scope.
        </p>
      )}
      {done.length ? (
        <div className="space-y-2">
          <button
            type="button"
            className="flex w-full items-center gap-1.5 rounded-md py-1 text-left text-xs font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-expanded={recentDoneExpanded}
            onClick={() =>
              setExpandedRecentDoneScope((scope) =>
                scope === recentDoneScope ? null : recentDoneScope,
              )
            }
          >
            {recentDoneExpanded ? (
              <ChevronDownIcon className="size-3.5 shrink-0" aria-hidden />
            ) : (
              <ChevronRightIcon className="size-3.5 shrink-0" aria-hidden />
            )}
            <span>Recently done ({done.length})</span>
          </button>
          {recentDoneExpanded ? (
            <ul className="space-y-2">
              {done.map((item) => (
                <ItemRow key={item.id} taskId={taskId} item={item} workers={dashboard.workers} />
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

/** Backwards-compatible entry point for callers that still provide the former lane-shaped props. */
export function TaskCardWorkers({
  taskId,
  inboxItems,
  workers,
}: {
  taskId: string;
  inboxItems: TaskItemSummary[];
  workers: TaskWorkerLane[];
}) {
  const dashboard: TaskDashboard = {
    task: {
      id: taskId,
      title: "",
      description: null,
      state: "active",
      manager_conversation_id: null,
    },
    derived: { has_running_workers: false },
    inbox_items: inboxItems,
    reconcile_queue_count: 0,
    assets: [],
    workers,
  };
  return <TaskItemsPanel taskId={taskId} dashboard={dashboard} selectedWorkerId={null} />;
}
