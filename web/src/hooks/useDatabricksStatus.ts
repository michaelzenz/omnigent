import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { hostFetch } from "@/lib/host";

export interface DatabricksStatus {
  connected: boolean;
  profile: string | null;
  host: string | null;
  error: string | null;
}

const STATUS_KEY = ["databricks-status"];

async function fetchDatabricksStatus(): Promise<DatabricksStatus> {
  const res = await hostFetch("/v1/databricks/status");
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as DatabricksStatus;
}

/** Poll Databricks connection status every 30 s for the sidebar indicator. */
export function useDatabricksStatus() {
  return useQuery({
    queryKey: STATUS_KEY,
    queryFn: fetchDatabricksStatus,
    refetchInterval: 30_000,
    staleTime: 15_000,
  });
}

interface LoginStartResponse {
  auth_url: string | null;
  profile?: string;
  error?: string;
}

/** Start `databricks auth login` server-side and get the OAuth URL.
 *  When `host` is undefined, the CLI reads the host from ~/.databrickscfg. */
export function useDatabricksLogin() {
  return useMutation({
    mutationFn: async (host?: string): Promise<LoginStartResponse> => {
      const res = await hostFetch("/v1/databricks/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(host ? { host } : {}),
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return (await res.json()) as LoginStartResponse;
    },
  });
}

interface LoginPollResponse {
  completed: boolean;
  success?: boolean;
  profile?: string;
  error?: string;
}

/** Poll the server to check if the login subprocess has finished. */
export function useDatabricksLoginPoll() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<LoginPollResponse> => {
      const res = await hostFetch("/v1/databricks/login/poll");
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return (await res.json()) as LoginPollResponse;
    },
    onSuccess: (data) => {
      if (data.completed && data.success) {
        queryClient.invalidateQueries({ queryKey: STATUS_KEY });
      }
    },
  });
}
