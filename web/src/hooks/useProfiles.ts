import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authenticatedFetch } from "@/lib/identity";
import { capitalizeAgentName } from "@/lib/agentLabels";
import type { AvailableAgent } from "@/hooks/useAvailableAgents";

interface AgentListWire {
  data: AgentWire[];
  has_more?: boolean;
  last_id?: string | null;
}

interface AgentWire {
  id: string;
  name: string;
  description?: string | null;
  harness?: string | null;
  skills?: { name: string; description: string }[];
  builtin?: boolean;
  enabled?: boolean;
  archived?: boolean;
  is_multi_agent?: boolean;
  subagent_count?: number;
  default_harness?: string | null;
  default_model?: string | null;
  created_at?: number | null;
}

function mapAgent(agent: AgentWire): AvailableAgent {
  return {
    id: agent.id,
    name: agent.name,
    display_name: capitalizeAgentName(agent.name),
    description: agent.description ?? null,
    harness: agent.harness ?? null,
    skills: agent.skills ?? [],
    builtin: agent.builtin ?? false,
    enabled: agent.enabled ?? true,
    archived: agent.archived ?? false,
    is_multi_agent: agent.is_multi_agent ?? false,
    subagent_count: agent.subagent_count ?? 0,
    default_harness: agent.default_harness ?? null,
    default_model: agent.default_model ?? null,
    ...(agent.created_at !== undefined ? { created_at: agent.created_at } : {}),
  };
}

async function profileApiError(response: Response, fallback: string): Promise<Error> {
  const body = (await response.json().catch(() => null)) as
    | { detail?: string; error?: { message?: string } }
    | null;
  return new Error(body?.error?.message ?? body?.detail ?? `${fallback} (${response.status})`);
}

async function fetchProfiles(): Promise<AvailableAgent[]> {
  const rows: AgentWire[] = [];
  let after: string | null = null;
  /* oxlint-disable no-await-in-loop */
  do {
    const params = new URLSearchParams({ include_disabled: "true", limit: "1000" });
    if (after) params.set("after", after);
    const response = await authenticatedFetch(`/v1/agents?${params.toString()}`);
    if (!response.ok) throw await profileApiError(response, "Couldn't load profiles");
    const body = (await response.json()) as AgentListWire;
    rows.push(...body.data);
    after = body.has_more === true && body.last_id ? body.last_id : null;
  } while (after);
  /* oxlint-enable no-await-in-loop */
  return rows.map(mapAgent);
}

async function updateProfileEnabled(input: {
  id: string;
  enabled: boolean;
}): Promise<AvailableAgent> {
  const response = await authenticatedFetch(`/v1/agents/${encodeURIComponent(input.id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: input.enabled }),
  });
  if (!response.ok) throw await profileApiError(response, "Couldn't update profile");
  return mapAgent((await response.json()) as AgentWire);
}

async function archiveProfile(id: string): Promise<void> {
  const response = await authenticatedFetch(`/v1/agents/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!response.ok) throw await profileApiError(response, "Couldn't delete profile");
}

async function createProfile(bundle: File): Promise<AvailableAgent> {
  const form = new FormData();
  form.append("bundle", bundle);
  const response = await authenticatedFetch("/v1/agents", { method: "POST", body: form });
  if (!response.ok) throw await profileApiError(response, "Couldn't create profile");
  return mapAgent((await response.json()) as AgentWire);
}

export async function autoSelectProfile(input: string): Promise<AvailableAgent> {
  const response = await authenticatedFetch("/v1/agents/auto-select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input }),
  });
  if (!response.ok) {
    throw await profileApiError(response, "Auto Select failed");
  }
  const body = (await response.json()) as { profile: AgentWire; reason: null };
  return mapAgent(body.profile);
}

export function useProfiles(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ["profiles", "include-disabled"],
    queryFn: fetchProfiles,
    staleTime: 30_000,
    enabled: options.enabled ?? true,
  });
}

export function useUpdateProfileEnabled() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: updateProfileEnabled,
    onSuccess: async (updated) => {
      queryClient.setQueryData<AvailableAgent[]>(["profiles", "include-disabled"], (current) =>
        current?.map((profile) => (profile.id === updated.id ? updated : profile)),
      );
      if (!updated.enabled) {
        queryClient.setQueryData<AvailableAgent[]>(["available-agents"], (current) =>
          current?.filter((agent) => agent.id !== updated.id),
        );
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["profiles"] }),
        queryClient.invalidateQueries({ queryKey: ["available-agents"] }),
      ]);
    },
  });
}

export function useArchiveProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: archiveProfile,
    onSuccess: async (_, id) => {
      queryClient.setQueryData<AvailableAgent[]>(["profiles", "include-disabled"], (current) =>
        current?.filter((profile) => profile.id !== id),
      );
      queryClient.setQueryData<AvailableAgent[]>(["available-agents"], (current) =>
        current?.filter((agent) => agent.id !== id),
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["profiles"] }),
        queryClient.invalidateQueries({ queryKey: ["available-agents"] }),
      ]);
    },
  });
}

export function useCreateProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createProfile,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["profiles"] }),
        queryClient.invalidateQueries({ queryKey: ["available-agents"] }),
      ]);
    },
  });
}
