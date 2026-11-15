import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authenticatedFetch } from "@/lib/identity";
import { apiErrorFromResponse } from "@/lib/sessionsApi";

/** When the custodian gets its daily session cleanup message. */
export interface CustodianSweepSettings {
  enabled: boolean;
  /** A time of day on a 24-hour clock in ``timezone``, e.g. ``"09:00"``. */
  time: string;
  /** An IANA timezone, e.g. ``"America/Los_Angeles"``. */
  timezone: string;
}

/** The user's Global Hub settings; the host and workspace are both null until set. */
export interface GlobalHubSettings {
  host_id: string | null;
  workspace: string | null;
  /** Whether sessions on no task report their finished turns to the broker. */
  report_new_sessions: boolean;
  /** The daily session cleanup, or null until the user saves it. */
  custodian_sweep?:
    | (CustodianSweepSettings & {
        /** Unix epoch seconds of the next cleanup; null while it is off. */
        next_run_at: number | null;
      })
    | null;
}

/** A save of the settings; a field left out stays as it is. */
export type GlobalHubSettingsUpdate = Pick<GlobalHubSettings, "host_id" | "workspace"> & {
  report_new_sessions?: boolean;
  custodian_sweep?: CustodianSweepSettings;
};

export const GLOBALHUB_SETTINGS_QUERY_KEY = ["globalhub-settings"] as const;

const SETTINGS_PATH = "/v1/settings/globalhub";

async function fetchGlobalHubSettings(): Promise<GlobalHubSettings> {
  const res = await authenticatedFetch(SETTINGS_PATH);
  if (!res.ok) throw await apiErrorFromResponse(res);
  return (await res.json()) as GlobalHubSettings;
}

async function saveGlobalHubSettings(
  settings: GlobalHubSettingsUpdate,
): Promise<GlobalHubSettings> {
  const res = await authenticatedFetch(SETTINGS_PATH, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  if (!res.ok) throw await apiErrorFromResponse(res);
  return (await res.json()) as GlobalHubSettings;
}

/** Where the user's Global Hub role sessions run, and whether new sessions report to the broker. */
export function useGlobalHubSettings() {
  return useQuery({
    queryKey: GLOBALHUB_SETTINGS_QUERY_KEY,
    queryFn: fetchGlobalHubSettings,
    staleTime: 30_000,
  });
}

/** Save the host and workspace, or clear both with nulls, and the reports switch when given. */
export function useSaveGlobalHubSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: saveGlobalHubSettings,
    onSuccess: (saved) => queryClient.setQueryData(GLOBALHUB_SETTINGS_QUERY_KEY, saved),
  });
}
