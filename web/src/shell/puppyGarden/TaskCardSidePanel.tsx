import type { AvailableAgent } from "@/hooks/useAvailableAgents";
import type { TaskDashboard, TaskExecutionSummary } from "@/lib/agentTasksApi";
import { TaskCardItemDetail } from "./TaskCardItemDetail";
import { TaskCardSessions } from "./TaskCardSessions";

interface TaskCardSidePanelProps {
  dashboard: TaskDashboard;
  taskId: string;
  agents: AvailableAgent[];
  defaultModel: string;
  selectedExecution: TaskExecutionSummary | null;
  onClearSelection: () => void;
}

export function TaskCardSidePanel({
  dashboard,
  taskId,
  agents,
  defaultModel,
  selectedExecution,
  onClearSelection,
}: TaskCardSidePanelProps) {
  const workerProfileIds = dashboard.workers.map((group) => group.profile_id);

  if (selectedExecution) {
    return (
      <aside className="flex min-h-0 w-[260px] shrink-0 flex-col border-l border-border bg-muted/20">
        <TaskCardItemDetail
          taskId={taskId}
          execution={selectedExecution}
          workerAgentIds={workerProfileIds}
          workerLanes={dashboard.workers}
          agents={agents}
          defaultModel={defaultModel}
          onClose={onClearSelection}
        />
      </aside>
    );
  }

  return <TaskCardSessions dashboard={dashboard} />;
}
