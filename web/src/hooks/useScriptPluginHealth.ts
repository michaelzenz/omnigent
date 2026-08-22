import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { showToast } from "@/components/ui/toast";
import {
  fetchScriptPluginHealth,
  type ScriptPluginHealthRow,
  type ScriptPluginKind,
  updateScriptPollPlugin,
} from "@/lib/agentTasksApi";

/** Polls the script-plugin health board for the given kind (poll | timer). */
export function useScriptPluginHealth(kind: ScriptPluginKind) {
  return useQuery<ScriptPluginHealthRow[]>({
    queryKey: ["script-plugin-health", kind],
    queryFn: () => fetchScriptPluginHealth(kind),
    refetchInterval: 15_000,
  });
}

export function useUpdateScriptPollPlugin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      hostId,
      name,
      enabled,
    }: {
      hostId: string;
      name: string;
      enabled: boolean;
    }) => updateScriptPollPlugin(hostId, name, enabled),
    onMutate: async ({ hostId, name, enabled }) => {
      const key = ["script-plugin-health", "poll"] as const;
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<ScriptPluginHealthRow[]>(key);
      queryClient.setQueryData<ScriptPluginHealthRow[]>(key, (rows) =>
        rows?.map((row) =>
          row.host_id === hostId && row.name === name ? { ...row, enabled } : row,
        ),
      );
      return { previous };
    },
    onError: (error, _variables, context) => {
      if (context?.previous) {
        queryClient.setQueryData(["script-plugin-health", "poll"], context.previous);
      }
      showToast(error instanceof Error ? error.message : "Failed to update poller");
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["script-plugin-health", "poll"] });
    },
  });
}
