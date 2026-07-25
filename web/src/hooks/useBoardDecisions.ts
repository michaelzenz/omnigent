import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchBoardDecisions,
  resolveRoutingProposal,
  type RoutingResolution,
} from "@/lib/agentTasksApi";

export function useBoardDecisions() {
  return useQuery({
    queryKey: ["agent-task-board-decisions"],
    queryFn: fetchBoardDecisions,
    refetchInterval: 10_000,
  });
}

export function useResolveRoutingProposal() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      itemId,
      resolution,
      selectedTaskId,
      instructions,
    }: {
      itemId: string;
      resolution: RoutingResolution;
      selectedTaskId?: string;
      instructions?: string;
    }) => {
      await resolveRoutingProposal(itemId, {
        resolution,
        selected_task_id: selectedTaskId,
        instructions,
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["agent-task-board-decisions"] });
      await queryClient.invalidateQueries({ queryKey: ["agent-tasks"] });
      await queryClient.invalidateQueries({ queryKey: ["agent-task-dashboard"] });
    },
  });
}
