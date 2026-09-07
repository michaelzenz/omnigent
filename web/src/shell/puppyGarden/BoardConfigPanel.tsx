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
  MANAGER_ROLE_PREFIX,
  TASK_BROKER_ROLE,
  fetchDispatchStoplist,
  fetchRoleProfiles,
  fetchTaskEventStats,
  setRoleDispatchStopped,
} from "@/lib/agentTasksApi";

// Non-terminal pipeline states in lifecycle order; anything else the server
// reports is appended after so a new state is never silently dropped.
const EVENT_STATUS_ORDER = [
  "received",
  "broadcast",
  "awaiting_grouping",
  "pending_triage",
  "routed",
  "classified_fyi",
] as const;

const EVENT_STATUS_LABELS: Record<string, string> = {
  received: "Received",
  broadcast: "Broadcast",
  awaiting_grouping: "Awaiting grouping",
  pending_triage: "Pending triage",
  routed: "Routed",
  classified_fyi: "Classified FYI",
};

function eventStatusRows(
  stats: { state: string; count: number }[],
): { state: string; label: string; count: number }[] {
  const byState = new Map(stats.map((entry) => [entry.state, entry.count]));
  const known = EVENT_STATUS_ORDER.filter((state) => byState.has(state)).map((state) => ({
    state,
    label: EVENT_STATUS_LABELS[state] ?? state,
    count: byState.get(state) ?? 0,
  }));
  const extra = [...byState.keys()]
    .filter((state) => !EVENT_STATUS_ORDER.includes(state as (typeof EVENT_STATUS_ORDER)[number]))
    .sort()
    .map((state) => ({
      state,
      label: EVENT_STATUS_LABELS[state] ?? state,
      count: byState.get(state) ?? 0,
    }));
  return [...known, ...extra];
}

/**
 * Gear button (top right of the board header) opening the board configuration
 * popup: dispatcher toggles for the broker and every manager role, plus live
 * event counts grouped by in-flight status. Toggling a role off puts it on the
 * global dispatch stoplist: its queues keep their items but nothing is
 * dispatched until it is re-enabled. Optimistic update, reverted if the PUT
 * fails.
 */
export function BoardConfigPanel({ disabled = false }: { disabled?: boolean }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const enabled = open && !disabled;
  const { data: stoppedRoles = [] } = useQuery({
    queryKey: ["dispatch-stoplist"],
    queryFn: fetchDispatchStoplist,
    enabled,
  });
  const { data: managerRoles = [] } = useQuery({
    queryKey: ["role-profiles", MANAGER_ROLE_PREFIX],
    queryFn: () => fetchRoleProfiles(MANAGER_ROLE_PREFIX),
    enabled,
  });
  const { data: eventStats = [] } = useQuery({
    queryKey: ["task-event-stats"],
    queryFn: fetchTaskEventStats,
    enabled,
    // Poll only while the popup is open; a cached query keeps its interval
    // after `enabled` flips off unless the interval itself is cleared.
    refetchInterval: enabled ? 10_000 : false,
  });
  const mutation = useMutation({
    mutationFn: ({ role, next }: { role: string; next: boolean }) =>
      setRoleDispatchStopped(role, next),
    onMutate: async ({ role, next }) => {
      await queryClient.cancelQueries({ queryKey: ["dispatch-stoplist"] });
      const previous = queryClient.getQueryData<string[]>(["dispatch-stoplist"]);
      queryClient.setQueryData<string[]>(["dispatch-stoplist"], (old = []) =>
        next ? [...new Set([...old, role])] : old.filter((entry) => entry !== role),
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

  const configurableRoles = [
    { role: TASK_BROKER_ROLE, label: "Broker" },
    ...[...managerRoles]
      .sort((a, b) => a.role.localeCompare(b.role))
      .map((profile) => ({ role: profile.role, label: profile.title ?? profile.role })),
  ];
  const statusRows = eventStatusRows(eventStats);

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
          <DialogDescription>Dispatcher and event pipeline for this workspace.</DialogDescription>
        </DialogHeader>

        <section className="space-y-1">
          <p className="text-sm font-medium">Dispatcher</p>
          <p className="text-xs text-muted-foreground">
            Stopped roles keep their items queued — nothing is dispatched until re-enabled.
          </p>
          <div className="space-y-2 pt-1">
            {configurableRoles.map(({ role, label }) => (
              <label
                key={role}
                className="flex items-center justify-between gap-4 rounded-md px-1 py-1"
              >
                <span className="truncate text-sm">{label}</span>
                <Switch
                  checked={!stoppedRoles.includes(role)}
                  disabled={disabled || mutation.isPending}
                  onCheckedChange={(checked) => mutation.mutate({ role, next: !checked })}
                />
              </label>
            ))}
          </div>
        </section>

        <section className="space-y-1">
          <p className="text-sm font-medium">Events</p>
          <p className="text-xs text-muted-foreground">
            In-flight counts by state; terminal states (reconciled, dismissed, failed) are excluded.
          </p>
          <div className="space-y-2 pt-1">
            {statusRows.length === 0 ? (
              <p className="px-1 py-1 text-sm text-muted-foreground">No in-flight events.</p>
            ) : (
              statusRows.map(({ state, label, count }) => (
                <div
                  key={state}
                  className="flex items-center justify-between gap-4 rounded-md px-1 py-1"
                >
                  <span className="text-sm">{label}</span>
                  <span className="text-sm font-medium tabular-nums text-muted-foreground">
                    {count}
                  </span>
                </div>
              ))
            )}
          </div>
        </section>
      </DialogContent>
    </Dialog>
  );
}
