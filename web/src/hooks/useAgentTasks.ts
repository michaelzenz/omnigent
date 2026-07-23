import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchAgentTasks,
  fetchSecretaryProfile,
  fetchTaskDashboard,
  resolveTaskEvent,
  type DispatchPayload,
  type ProposalResolution,
} from "@/lib/agentTasksApi";

export function useAgentTaskList(state = "active") {
  return useQuery({
    queryKey: ["agent-tasks", state],
    queryFn: () => fetchAgentTasks(state),
    refetchInterval: 10_000,
  });
}

export function useTaskDashboard(taskId: string) {
  return useQuery({
    queryKey: ["agent-task-dashboard", taskId],
    queryFn: () => fetchTaskDashboard(taskId),
    refetchInterval: 10_000,
  });
}

export function useSecretaryProfile() {
  return useQuery({
    queryKey: ["agent-task-secretary-profile"],
    queryFn: fetchSecretaryProfile,
    staleTime: 60_000,
    retry: false,
  });
}

export function useResolveTaskProposal(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      eventId,
      resolution,
      edited_payload,
    }: {
      eventId: string;
      resolution: ProposalResolution;
      edited_payload?: DispatchPayload;
    }) => {
      await resolveTaskEvent(eventId, { resolution, edited_payload });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["agent-task-dashboard", taskId] });
    },
  });
}
