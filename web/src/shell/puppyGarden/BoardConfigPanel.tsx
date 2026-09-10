import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BanIcon, BotIcon, Loader2Icon, RotateCcwIcon, SettingsIcon, XIcon } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  TASK_BROKER_ROLE,
  type TaskManagerSummary,
  deleteManager,
  dismissQueueBacklog,
  fetchAgentTasks,
  fetchDispatchStoplist,
  fetchEventBacklog,
  fetchManagers,
  resetBrokerSession,
  setRoleDispatchStopped,
  spawnManagerNotice,
} from "@/lib/agentTasksApi";

// The dispatcher's stoplist stores bare role keys ("broker") or
// scope-qualified keys ("manager:<manager_id>") for a single queue.
function stopKey(role: string, scopeId: string | null | undefined): string {
  return scopeId ? `${role}:${scopeId}` : role;
}

const OTHER_STATE_LABELS: Record<string, string> = {
  received: "received",
  broadcast: "broadcast",
  classified_fyi: "FYI",
  routed_unassigned: "routed, no manager",
};

interface QueueRow {
  role: string;
  scopeId: string | null;
  label: string;
  count: number;
  /** Set for manager rows, carrying the row's manager for deletion. */
  manager?: TaskManagerSummary;
}

/**
 * Gear button (top right of the board header) opening the board configuration
 * popup: one row per dispatch queue — the broker and every registered manager
 * — with its waiting-event count and a dispatch toggle. Toggling a row off
 * puts that queue on the global dispatch stoplist: its items stay queued but
 * nothing is dispatched until it is re-enabled. Optimistic update, reverted
 * if the PUT fails.
 */
export function BoardConfigPanel({ disabled = false }: { disabled?: boolean }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const enabled = open && !disabled;
  const { data: stoppedKeys = [] } = useQuery({
    queryKey: ["dispatch-stoplist"],
    queryFn: fetchDispatchStoplist,
    enabled,
  });
  const { data: managers = [] } = useQuery({
    queryKey: ["agent-managers"],
    queryFn: fetchManagers,
    enabled,
  });
  const { data: backlog } = useQuery({
    queryKey: ["event-backlog"],
    queryFn: fetchEventBacklog,
    enabled,
    // Poll only while the popup is open; a cached query keeps its interval
    // after `enabled` flips off unless the interval itself is cleared.
    refetchInterval: enabled ? 10_000 : false,
  });
  const [managerPendingDelete, setManagerPendingDelete] = useState<TaskManagerSummary | null>(null);
  const [dismissTarget, setDismissTarget] = useState<{
    role: string;
    scopeId: string | null;
    label: string;
    count: number;
  } | null>(null);
  // Mirror the server's spawn-manager-notice eligibility (live_tasks = active
  // + pending + idle + agent-resolved with no manager) so the count and the
  // button enablement match what the endpoint would collect.
  const { data: unmanagedTasks = [] } = useQuery({
    queryKey: ["agent-tasks", "unmanaged"],
    queryFn: async () => {
      const [pending, active, idle, agentResolved] = await Promise.all([
        fetchAgentTasks("pending"),
        fetchAgentTasks("active"),
        fetchAgentTasks("idle"),
        fetchAgentTasks("agent-resolved"),
      ]);
      return [...pending, ...active, ...idle, ...agentResolved].filter((task) => !task.manager_id);
    },
    enabled,
  });
  const spawnNotice = useMutation({
    mutationFn: spawnManagerNotice,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["event-backlog"] });
      await queryClient.invalidateQueries({ queryKey: ["agent-tasks", "unmanaged"] });
    },
  });
  const resetBroker = useMutation({
    mutationFn: resetBrokerSession,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["agent-task-broker-profile"] });
      void queryClient.invalidateQueries({ queryKey: ["agent-task-broker-session"] });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
  const deleteManagerMutation = useMutation({
    mutationFn: (managerId: string) => deleteManager(managerId),
    onSuccess: async (_data, managerId) => {
      // Drop the deleted manager's scoped stop entry so the stoplist stays clean.
      queryClient.setQueryData<string[]>(["dispatch-stoplist"], (old = []) =>
        old.filter((key) => key !== stopKey("manager", managerId)),
      );
      await queryClient.invalidateQueries({ queryKey: ["agent-managers"] });
      await queryClient.invalidateQueries({ queryKey: ["event-backlog"] });
      await queryClient.invalidateQueries({ queryKey: ["dispatch-stoplist"] });
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      await queryClient.invalidateQueries({ queryKey: ["agent-tasks"] });
    },
    onSettled: () => setManagerPendingDelete(null),
  });
  const dismissBacklogMutation = useMutation({
    mutationFn: ({ role, scopeId }: { role: string; scopeId: string | null }) =>
      dismissQueueBacklog(role, scopeId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["event-backlog"] });
    },
    onSettled: () => setDismissTarget(null),
  });
  const mutation = useMutation({
    mutationFn: ({
      role,
      scopeId,
      next,
    }: {
      role: string;
      scopeId: string | null;
      next: boolean;
    }) => setRoleDispatchStopped(role, next, scopeId),
    onMutate: async ({ role, scopeId, next }) => {
      await queryClient.cancelQueries({ queryKey: ["dispatch-stoplist"] });
      const previous = queryClient.getQueryData<string[]>(["dispatch-stoplist"]);
      const key = stopKey(role, scopeId);
      queryClient.setQueryData<string[]>(["dispatch-stoplist"], (old = []) =>
        next ? [...new Set([...old, key])] : old.filter((entry) => entry !== key),
      );
      return { previous };
    },
    onError: (_error, _vars, context) => {
      if (context?.previous) {
        queryClient.setQueryData(["dispatch-stoplist"], context.previous);
      }
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["dispatch-stoplist"] }),
  });

  const countByScope = new Map(
    (backlog?.data ?? []).map((row) => [row.scope_id ?? row.role, row.count]),
  );
  const rows: QueueRow[] = [
    {
      role: TASK_BROKER_ROLE,
      scopeId: null,
      label: "Broker",
      count: countByScope.get(TASK_BROKER_ROLE) ?? 0,
    },
    ...[...managers]
      .sort((a, b) => a.title.localeCompare(b.title))
      .map((manager) => ({
        role: "manager",
        scopeId: manager.id,
        label: manager.title || manager.role_key,
        count: countByScope.get(manager.id) ?? 0,
        manager,
      })),
  ];
  const other = backlog?.other ?? {};
  const otherEntries = Object.entries(other).filter(([, count]) => count > 0);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <button
          type="button"
          aria-label="Board configuration"
          title="Board configuration"
          className="inline-flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          // The board scroll container closes the chat context on any click;
          // the gear must not.
          onClick={(event) => event.stopPropagation()}
        >
          <SettingsIcon className="size-4" />
        </button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Board configuration</DialogTitle>
          <DialogDescription>
            Dispatch queues and their waiting events for this workspace.
          </DialogDescription>
        </DialogHeader>

        <section className="space-y-1">
          <p className="text-sm font-medium">Queues</p>
          <p className="text-xs text-muted-foreground">
            Events waiting per queue. A stopped queue keeps its items — nothing is dispatched until
            re-enabled.
          </p>
          <div className="space-y-2 pt-1">
            {rows.map(({ role, scopeId, label, count, manager }) => {
              const key = stopKey(role, scopeId);
              return (
                <div
                  key={key}
                  className="flex items-center justify-between gap-4 rounded-md px-1 py-1"
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="truncate text-sm">{label}</span>
                    <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                      {count}
                    </span>
                  </span>
                  <span className="flex shrink-0 items-center gap-1">
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      aria-label={`Dismiss all waiting events for ${label}`}
                      title="Dismiss all waiting events"
                      disabled={disabled || dismissBacklogMutation.isPending || count === 0}
                      onClick={() => setDismissTarget({ role, scopeId, label, count })}
                    >
                      <BanIcon className="size-3.5 text-destructive" />
                      <span className="sr-only">Dismiss all waiting events</span>
                    </Button>
                    {manager ? (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-xs"
                        aria-label={`Delete manager ${manager.title || manager.role_key}`}
                        title="Delete manager and its session"
                        disabled={disabled || deleteManagerMutation.isPending}
                        onClick={() => setManagerPendingDelete(manager)}
                      >
                        <XIcon className="size-3.5 text-destructive" />
                        <span className="sr-only">Delete manager</span>
                      </Button>
                    ) : (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-xs"
                        aria-label="Reset broker session"
                        title="Reset broker: delete its session and start a fresh one"
                        disabled={disabled || resetBroker.isPending}
                        onClick={() => resetBroker.mutate()}
                      >
                        <RotateCcwIcon
                          className={`size-3.5 text-destructive ${resetBroker.isPending ? "animate-spin" : ""}`}
                        />
                        <span className="sr-only">Reset broker</span>
                      </Button>
                    )}
                    <Switch
                      checked={!stoppedKeys.includes(key)}
                      disabled={disabled || mutation.isPending}
                      onCheckedChange={(checked) =>
                        mutation.mutate({ role, scopeId, next: !checked })
                      }
                    />
                  </span>
                </div>
              );
            })}
          </div>
        </section>

        {otherEntries.length > 0 ? (
          <p className="text-xs text-muted-foreground">
            Elsewhere:{" "}
            {otherEntries
              .map(([state, count]) => `${OTHER_STATE_LABELS[state] ?? state} ${count}`)
              .join(", ")}
          </p>
        ) : null}

        <section className="space-y-2 border-t pt-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-medium">Unmanaged tasks</p>
              <p className="text-xs text-muted-foreground">
                {unmanagedTasks.length === 0
                  ? "Every live task has a manager."
                  : `${unmanagedTasks.length} live task${unmanagedTasks.length === 1 ? "" : "s"} without a manager. One notice lists them all for the broker to assign to new/active manager(s).`}
              </p>
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="shrink-0"
              disabled={disabled || spawnNotice.isPending || unmanagedTasks.length === 0}
              onClick={() => spawnNotice.mutate()}
            >
              {spawnNotice.isPending ? <Loader2Icon className="animate-spin" /> : <BotIcon />}
              Reassign Unmanaged Tasks
            </Button>
          </div>
          {spawnNotice.isError ? (
            <p className="text-xs text-destructive">{String(spawnNotice.error)}</p>
          ) : null}
          {spawnNotice.data ? (
            <p className="text-xs text-muted-foreground">
              {spawnNotice.data.unmanaged_count === 0
                ? "No unmanaged tasks — nothing sent."
                : `Broker notice queued for ${spawnNotice.data.unmanaged_count} task${spawnNotice.data.unmanaged_count === 1 ? "" : "s"}.`}
              {spawnNotice.data.superseded > 0
                ? ` Replaced ${spawnNotice.data.superseded} pending request${spawnNotice.data.superseded === 1 ? "" : "s"}.`
                : ""}
            </p>
          ) : null}
        </section>
      </DialogContent>

      <Dialog
        open={dismissTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDismissTarget(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="text-destructive">
              Dismiss {dismissTarget?.count ?? 0} waiting event
              {(dismissTarget?.count ?? 0) === 1 ? "" : "s"} for {dismissTarget?.label}?
            </DialogTitle>
            <DialogDescription>
              {dismissTarget?.role === TASK_BROKER_ROLE
                ? "All events waiting on the broker (awaiting grouping + pending triage) are marked dismissed. Queued broker notices are cancelled; in-flight notices finish naturally. This cannot be undone."
                : "All routed events addressed to this manager are marked dismissed, and its queued notices are cancelled. This cannot be undone."}
            </DialogDescription>
          </DialogHeader>
          {dismissBacklogMutation.isError ? (
            <p className="text-sm text-destructive">{String(dismissBacklogMutation.error)}</p>
          ) : null}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={dismissBacklogMutation.isPending}
              onClick={() => setDismissTarget(null)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={dismissBacklogMutation.isPending}
              onClick={() => {
                if (dismissTarget) {
                  dismissBacklogMutation.mutate({
                    role: dismissTarget.role,
                    scopeId: dismissTarget.scopeId,
                  });
                }
              }}
            >
              {dismissBacklogMutation.isPending ? (
                <Loader2Icon className="mr-2 size-4 animate-spin" />
              ) : (
                <BanIcon className="mr-2 size-4" />
              )}
              Dismiss all
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={managerPendingDelete !== null}
        onOpenChange={(next) => {
          if (!next) setManagerPendingDelete(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="text-destructive">
              Delete manager {managerPendingDelete?.title || managerPendingDelete?.role_key}?
            </DialogTitle>
            <DialogDescription>
              The manager session is deleted, its queued work is cancelled, and its tasks are
              detached (waiting events return to the broker for re-routing). This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          {deleteManagerMutation.isError ? (
            <p className="text-sm text-destructive">{String(deleteManagerMutation.error)}</p>
          ) : null}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={deleteManagerMutation.isPending}
              onClick={() => setManagerPendingDelete(null)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={deleteManagerMutation.isPending}
              onClick={() => {
                if (managerPendingDelete) {
                  deleteManagerMutation.mutate(managerPendingDelete.id);
                }
              }}
            >
              {deleteManagerMutation.isPending ? (
                <Loader2Icon className="mr-2 size-4 animate-spin" />
              ) : (
                <XIcon className="mr-2 size-4" />
              )}
              Delete manager
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Dialog>
  );
}
