import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authenticatedFetch } from "@/lib/identity";
import { apiErrorFromResponse, updateSession } from "@/lib/sessionsApi";
import { fetchBoardRoles } from "./useGlobalHubRoles";

/** A field of a role definition that the user can override. */
export type RoleField = "description" | "model" | "manual";

/** One of the user's GlobalHub role definitions, e.g. the broker. */
export interface RoleProfileSummary {
  role: string;
  kind: string;
  title: string;
  description: string;
  /** ``null`` while the role's sessions use the role agent's own model. */
  model: string | null;
  /** The fields the user overrode. */
  customized: RoleField[];
}

/** When a role's session gets its scheduled run, e.g. the custodian's session cleanup. */
export interface RoleScheduleSettings {
  enabled: boolean;
  /** An RFC 5545 rule without an end, e.g. ``"FREQ=DAILY;BYHOUR=9;BYMINUTE=0"``. */
  rrule: string;
  /** An IANA timezone, e.g. ``"America/Los_Angeles"``. */
  timezone: string;
}

/** A saved schedule; ``next_run_at`` is null while it is off. */
export interface RoleSchedule extends RoleScheduleSettings {
  /** Unix epoch seconds of the next run. */
  next_run_at: number | null;
}

/** A role definition with the manual sent on every turn, and what a reset restores. */
export interface RoleProfile extends RoleProfileSummary {
  manual: string;
  default_description: string;
  default_manual: string;
  /** Whether the user can schedule runs of the role's session. */
  schedulable: boolean;
  /** The saved schedule, or null until the user saves one. */
  schedule: RoleSchedule | null;
}

/** Fields to save; ``null`` restores a field's default, and a null schedule deletes it. */
export type RoleProfileChanges = Partial<Record<RoleField, string | null>> & {
  schedule?: RoleScheduleSettings | null;
};

/** A saved role, and how its running sessions took a new model. */
export interface RoleProfileSaved {
  profile: RoleProfile;
  /** How many running sessions switched to the new model. */
  switched: number;
  /** The titles of the running sessions that kept their model. */
  notSwitched: string[];
}

const ROLE_PROFILES_QUERY_KEY = ["globalhub-role-profiles"] as const;

function rolePath(role: string): string {
  return `/v1/agent-tasks/roles/${encodeURIComponent(role)}/profile`;
}

async function readJson<T>(res: Response): Promise<T> {
  if (!res.ok) throw await apiErrorFromResponse(res);
  return (await res.json()) as T;
}

/** The user's role definitions, without their manuals. */
export function useRoleProfiles() {
  return useQuery({
    queryKey: ROLE_PROFILES_QUERY_KEY,
    queryFn: async () =>
      (
        await readJson<{ data: RoleProfileSummary[] }>(
          await authenticatedFetch("/v1/agent-tasks/roles/profiles"),
        )
      ).data,
  });
}

/** One role definition with its manual and defaults. */
export function useRoleProfile(role: string) {
  return useQuery({
    queryKey: [...ROLE_PROFILES_QUERY_KEY, role],
    queryFn: async () => readJson<RoleProfile>(await authenticatedFetch(rolePath(role))),
  });
}

/**
 * Save changes to a role. A new model also goes to the role's running
 * sessions through main's session settings, as the session page's model
 * picker sends it.
 */
export function useSaveRoleProfile(role: string) {
  const queryClient = useQueryClient();
  return useMutation<RoleProfileSaved, Error, RoleProfileChanges>({
    mutationFn: async (changes) => {
      const profile = await readJson<RoleProfile>(
        await authenticatedFetch(rolePath(role), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(changes),
        }),
      );
      if (!("model" in changes)) return { profile, switched: 0, notSwitched: [] };
      return { profile, ...(await switchRunningSessions(role, profile.model)) };
    },
    onSuccess: ({ profile }) => {
      queryClient.setQueryData([...ROLE_PROFILES_QUERY_KEY, role], profile);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ROLE_PROFILES_QUERY_KEY }),
  });
}

async function switchRunningSessions(
  role: string,
  model: string | null,
): Promise<Pick<RoleProfileSaved, "switched" | "notSwitched">> {
  const running = (await fetchBoardRoles()).filter(
    (row) => row.role === role && row.session_id !== null,
  );
  const results = await Promise.allSettled(
    running.map((row) => updateSession(row.session_id as string, { modelOverride: model })),
  );
  return {
    switched: results.filter((result) => result.status === "fulfilled").length,
    notSwitched: running
      .filter((_row, index) => results[index].status === "rejected")
      .map((row) => row.title),
  };
}
