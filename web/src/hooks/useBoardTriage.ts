import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  acceptTaskPackage,
  fetchBoardTriage,
  rejectTaskPackage,
  resolveFyiCluster,
  type FyiResolution,
} from "@/lib/agentTasksApi";

const BOARD_TRIAGE_KEY = ["agent-task-board-triage"] as const;

export function useBoardTriage() {
  return useQuery({
    queryKey: BOARD_TRIAGE_KEY,
    queryFn: fetchBoardTriage,
    refetchInterval: 10_000,
  });
}

async function invalidateBoard(queryClient: ReturnType<typeof useQueryClient>) {
  await queryClient.invalidateQueries({ queryKey: BOARD_TRIAGE_KEY });
  await queryClient.invalidateQueries({ queryKey: ["agent-tasks"] });
  await queryClient.invalidateQueries({ queryKey: ["agent-task-dashboard"] });
}

export function useAcceptTaskPackage() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (taskId: string) => {
      await acceptTaskPackage(taskId);
    },
    onSuccess: async () => {
      await invalidateBoard(queryClient);
    },
  });
}

export function useRejectTaskPackage() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (taskId: string) => {
      await rejectTaskPackage(taskId);
    },
    onSuccess: async () => {
      await invalidateBoard(queryClient);
    },
  });
}

export function useResolveFyiCluster() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      clusterId,
      resolution,
      routingTitle,
      routingInstructions,
      suggestedTaskId,
      proposedTaskTitle,
      proposedTaskCharter,
    }: {
      clusterId: string;
      resolution: FyiResolution;
      routingTitle?: string;
      routingInstructions?: string;
      suggestedTaskId?: string | null;
      proposedTaskTitle?: string;
      proposedTaskCharter?: string;
    }) => {
      await resolveFyiCluster(clusterId, {
        resolution,
        routing_title: routingTitle,
        routing_instructions: routingInstructions,
        suggested_task_id: suggestedTaskId,
        proposed_task_title: proposedTaskTitle,
        proposed_task_charter: proposedTaskCharter,
      });
    },
    onSuccess: async () => {
      await invalidateBoard(queryClient);
    },
  });
}
