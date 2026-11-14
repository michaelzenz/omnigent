import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authenticatedFetch } from "@/lib/identity";
import { apiErrorFromResponse } from "@/lib/sessionsApi";

/** Where the user's Global Hub role sessions run; both null until it is set. */
export interface GlobalHubSettings {
  host_id: string | null;
  workspace: string | null;
}

export const GLOBALHUB_SETTINGS_QUERY_KEY = ["globalhub-settings"] as const;

const SETTINGS_PATH = "/v1/settings/globalhub";

async function fetchGlobalHubSettings(): Promise<GlobalHubSettings> {
  const res = await authenticatedFetch(SETTINGS_PATH);
  if (!res.ok) throw await apiErrorFromResponse(res);
  return (await res.json()) as GlobalHubSettings;
}

async function saveGlobalHubSettings(settings: GlobalHubSettings): Promise<GlobalHubSettings> {
  const res = await authenticatedFetch(SETTINGS_PATH, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  if (!res.ok) throw await apiErrorFromResponse(res);
  return (await res.json()) as GlobalHubSettings;
}

/** The host and workspace where the user's Global Hub role sessions run. */
export function useGlobalHubSettings() {
  return useQuery({
    queryKey: GLOBALHUB_SETTINGS_QUERY_KEY,
    queryFn: fetchGlobalHubSettings,
    staleTime: 30_000,
  });
}

/** Save the host and workspace, or clear both with nulls. */
export function useSaveGlobalHubSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: saveGlobalHubSettings,
    onSuccess: (saved) => queryClient.setQueryData(GLOBALHUB_SETTINGS_QUERY_KEY, saved),
  });
}
