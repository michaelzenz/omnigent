import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchBoardTriage,
  resolveFyiCluster,
  resolveRoutingProposal,
  type FyiResolution,
  type RoutingResolution,
} from "@/lib/agentTasksApi";

const BOARD_TRIAGE_KEY = ["agent-task-board-triage"] as const;

export function useBoardTriage() {
  return useQuery({
    queryKey: BOARD_TRIAGE_KEY,
    queryFn: fetchBoardTriage,
    refetchInterval: 10_000,
  });
}

/** @deprecated Use useBoardTriage */
export function useBoardDecisions() {
  const query = useBoardTriage();
  return {
    ...query,
    data: query.data?.decisions,
  };
}

async function invalidateBoard(queryClient: ReturnType<typeof useQueryClient>) {
  await queryClient.invalidateQueries({ queryKey: BOARD_TRIAGE_KEY });
  await queryClient.invalidateQueries({ queryKey: ["agent-tasks"] });
  await queryClient.invalidateQueries({ queryKey: ["agent-task-dashboard"] });
}

export function useResolveRoutingProposal() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      itemId,
      resolution,
      selectedTaskId,
      instructions,
      proposedTaskTitle,
      proposedTaskCharter,
      proposedTaskDescription,
    }: {
      itemId: string;
      resolution: RoutingResolution;
      selectedTaskId?: string;
      instructions?: string;
      proposedTaskTitle?: string;
      proposedTaskCharter?: string;
      proposedTaskDescription?: string;
    }) => {
      await resolveRoutingProposal(itemId, {
        resolution,
        selected_task_id: selectedTaskId,
        instructions,
        proposed_task_title: proposedTaskTitle,
        proposed_task_charter: proposedTaskCharter,
        proposed_task_description: proposedTaskDescription,
      });
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
      recommendedTaskId,
      recommendNewTask,
      proposedTaskTitle,
      proposedTaskCharter,
    }: {
      clusterId: string;
      resolution: FyiResolution;
      routingTitle?: string;
      routingInstructions?: string;
      recommendedTaskId?: string;
      recommendNewTask?: boolean;
      proposedTaskTitle?: string;
      proposedTaskCharter?: string;
    }) => {
      await resolveFyiCluster(clusterId, {
        resolution,
        routing_title: routingTitle,
        routing_instructions: routingInstructions,
        recommended_task_id: recommendedTaskId,
        recommend_new_task: recommendNewTask,
        proposed_task_title: proposedTaskTitle,
        proposed_task_charter: proposedTaskCharter,
      });
    },
    onSuccess: async () => {
      await invalidateBoard(queryClient);
    },
  });
}
