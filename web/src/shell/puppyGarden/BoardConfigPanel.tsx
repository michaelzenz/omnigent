import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { SettingsIcon } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Switch } from "@/components/ui/switch";
import { fetchDispatchStoplist, setRoleDispatchStopped } from "@/lib/agentTasksApi";

// Roles the config panel exposes toggles for. The stoplist itself is
// role-generic — the dispatcher reads it wholesale — so extending the panel
// later is adding an entry here, nothing else.
const CONFIGURABLE_ROLES = [{ role: "broker", label: "Broker" }] as const;

/**
 * Gear button (top right of the board header) opening the dispatcher config
 * panel. Toggling a role off puts it on the global dispatch stoplist: its
 * queues keep their items but nothing is dispatched until it is re-enabled.
 * Optimistic update, reverted if the PUT fails.
 */
export function BoardConfigPanel({ disabled = false }: { disabled?: boolean }) {
  const queryClient = useQueryClient();
  const { data: stoppedRoles = [] } = useQuery({
    queryKey: ["dispatch-stoplist"],
    queryFn: fetchDispatchStoplist,
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

  return (
    <Popover>
      <PopoverTrigger asChild>
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
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72">
        <div className="space-y-1">
          <p className="text-sm font-medium">Dispatcher</p>
          <p className="text-xs text-muted-foreground">
            Stopped roles keep their items queued — nothing is dispatched until
            re-enabled.
          </p>
        </div>
        <div className="space-y-2 pt-1">
          {CONFIGURABLE_ROLES.map(({ role, label }) => (
            <label
              key={role}
              className="flex items-center justify-between gap-4 rounded-md px-1 py-1"
            >
              <span className="text-sm">{label}</span>
              <Switch
                checked={!stoppedRoles.includes(role)}
                disabled={disabled || mutation.isPending}
                onCheckedChange={(checked) => mutation.mutate({ role, next: !checked })}
              />
            </label>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
