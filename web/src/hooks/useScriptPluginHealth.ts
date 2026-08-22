import { useQuery } from "@tanstack/react-query";
import {
  fetchScriptPluginHealth,
  type ScriptPluginHealthRow,
  type ScriptPluginKind,
} from "@/lib/agentTasksApi";

/** Polls the script-plugin health board for the given kind (poll | timer). */
export function useScriptPluginHealth(kind: ScriptPluginKind) {
  return useQuery<ScriptPluginHealthRow[]>({
    queryKey: ["script-plugin-health", kind],
    queryFn: () => fetchScriptPluginHealth(kind),
    refetchInterval: 15_000,
  });
}
