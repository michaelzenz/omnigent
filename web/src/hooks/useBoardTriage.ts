import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchBoardTriage, resolveFyiCluster, type FyiResolution } from "@/lib/agentTasksApi";

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

export function useResolveFyiCluster() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ clusterId, resolution }: { clusterId: string; resolution: FyiResolution }) => {
      await resolveFyiCluster(clusterId, { resolution });
    },
    onSuccess: async () => {
      await invalidateBoard(queryClient);
    },
  });
}
