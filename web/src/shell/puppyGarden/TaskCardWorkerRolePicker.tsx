import { useState } from "react";
import { useUpdateAgentTaskWorkerRole } from "@/hooks/useAgentTasks";
import { WORKER_ROLE_PREFIX } from "@/lib/agentTasksApi";
import { TaskCardTemplateRolePicker } from "./TaskCardTemplateRolePicker";

interface TaskCardWorkerRolePickerProps {
  taskId: string;
  workerRoleKey: string;
  editable: boolean;
}

export function TaskCardWorkerRolePicker({
  taskId,
  workerRoleKey,
  editable,
}: TaskCardWorkerRolePickerProps) {
  const updateRole = useUpdateAgentTaskWorkerRole(taskId);
  const [error, setError] = useState<string | null>(null);

  return (
    <TaskCardTemplateRolePicker
      taskId={taskId}
      rolePrefix={WORKER_ROLE_PREFIX}
      roleKey={workerRoleKey}
      label="Worker role"
      editable={editable}
      isPending={updateRole.isPending}
      error={error}
      onChange={(role) => {
        setError(null);
        updateRole.mutate(role, {
          onError: (err) => {
            setError(err instanceof Error ? err.message : "Failed to update worker role");
          },
        });
      }}
    />
  );
}
