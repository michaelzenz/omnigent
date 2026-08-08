import { useState } from "react";
import { useUpdateAgentTaskManagerRole } from "@/hooks/useAgentTasks";
import { MANAGER_ROLE_PREFIX } from "@/lib/agentTasksApi";
import { TaskCardTemplateRolePicker } from "./TaskCardTemplateRolePicker";

interface TaskCardManagerRolePickerProps {
  taskId: string;
  managerRoleKey: string;
  editable: boolean;
}

export function TaskCardManagerRolePicker({
  taskId,
  managerRoleKey,
  editable,
}: TaskCardManagerRolePickerProps) {
  const updateRole = useUpdateAgentTaskManagerRole(taskId);
  const [error, setError] = useState<string | null>(null);

  return (
    <TaskCardTemplateRolePicker
      taskId={taskId}
      rolePrefix={MANAGER_ROLE_PREFIX}
      roleKey={managerRoleKey}
      label="Manager role"
      editable={editable}
      isPending={updateRole.isPending}
      error={error}
      onChange={(role) => {
        setError(null);
        updateRole.mutate(role, {
          onError: (err) => {
            setError(err instanceof Error ? err.message : "Failed to update manager role");
          },
        });
      }}
    />
  );
}
