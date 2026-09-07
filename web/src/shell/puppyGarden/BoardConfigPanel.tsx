import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { SettingsIcon } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Switch } from "@/components/ui/switch";
import {
  TASK_BROKER_ROLE,
  fetchDispatchStoplist,
  fetchEventBacklog,
  fetchManagers,
  setRoleDispatchStopped,
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
            {rows.map(({ role, scopeId, label, count }) => {
              const key = stopKey(role, scopeId);
              return (
                <label
                  key={key}
                  className="flex items-center justify-between gap-4 rounded-md px-1 py-1"
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="truncate text-sm">{label}</span>
                    <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                      {count}
                    </span>
                  </span>
                  <Switch
                    checked={!stoppedKeys.includes(key)}
                    disabled={disabled || mutation.isPending}
                    onCheckedChange={(checked) =>
                      mutation.mutate({ role, scopeId, next: !checked })
                    }
                  />
                </label>
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
      </DialogContent>
    </Dialog>
  );
}
