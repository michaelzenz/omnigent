import { useState } from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useUpdateWorkerLaneRole } from "@/hooks/useAgentTasks";
import { useRoleProfiles } from "@/hooks/useRoleProfiles";
import { WORKER_ROLE_PREFIX } from "@/lib/agentTasksApi";

interface WorkerLaneRolePickerProps {
  taskId: string;
  workerId: string;
  roleKey: string | null;
}

/** Lets a lane that has not run yet be pointed at a different worker role. */
export function WorkerLaneRolePicker({ taskId, workerId, roleKey }: WorkerLaneRolePickerProps) {
  const { data: roles = [] } = useRoleProfiles(WORKER_ROLE_PREFIX);
  const updateRole = useUpdateWorkerLaneRole(taskId);
  const [error, setError] = useState<string | null>(null);

  const onChange = (next: string) => {
    setError(null);
    updateRole.mutate(
      { workerId, roleKey: next },
      {
        onError: (err) => {
          setError(err instanceof Error ? err.message : "Failed to update worker role");
        },
      },
    );
  };

  return (
    <div className="flex shrink-0 flex-col items-end gap-0.5">
      <Select
        value={roleKey ?? ""}
        onValueChange={onChange}
        disabled={updateRole.isPending || roles.length === 0}
      >
        <SelectTrigger
          className="h-7 w-40 text-xs"
          size="sm"
          data-testid={`worker-lane-role-${workerId}`}
          onClick={(event) => event.stopPropagation()}
        >
          <SelectValue placeholder="Pick a role" />
        </SelectTrigger>
        <SelectContent>
          {roles.map((role) => (
            <SelectItem key={role.role} value={role.role}>
              {role.title ?? role.role}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {error ? <p className="text-[10px] text-destructive">{error}</p> : null}
    </div>
  );
}
